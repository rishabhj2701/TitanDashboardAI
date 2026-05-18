import json
import logging
import asyncio
import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

# Imports from your existing codebase
from .agents.sql_planner import get_sql_planner_agent
from .orchestration.sql_dispatcher import run_unified_sql_query
from .session_state import append_execution_event, get_active_session, get_analysis_context, get_execution_events
from .services.datasets import list_session_datasets

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .modeling import collect_usage_from_event, finalize_usage_collector, get_agent_model, new_usage_collector
logger = logging.getLogger(__name__)


def _safe_event_parts(event: Any) -> list[Any]:
    """Return event parts as a list, guarding against None/non-list payloads."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    if isinstance(parts, list):
        return parts
    try:
        return list(parts)
    except TypeError:
        return []


def _plan_operations(plan: dict[str, Any]) -> list[str]:
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        return []
    ops: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("operation") or "").strip().lower()
        if op:
            ops.append(op)
    return ops


def _query_explicitly_requests_rows(user_query: str) -> bool:
    text = str(user_query or "").strip().lower()
    if not text:
        return False
    tokens = (
        "head",
        "sample",
        "example row",
        "example rows",
        "first ",
        "top ",
        "table",
        "rows",
        "list ",
        "show row",
        "show rows",
        "preview",
    )
    return any(token in text for token in tokens)


def _is_crash_hard_brake_near_intent(user_query: str) -> bool:
    text = str(user_query or "").strip().lower()
    if not text:
        return False
    has_crash = ("crash" in text) or ("accident" in text)
    normalized = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    has_hard_brake = (
        "hard braking" in text
        or "hard brake" in text
        or "hardbraking" in normalized
        or "hardbrake" in normalized
    )
    has_near = any(token in text for token in (" near ", " nearby", " around ", "within ", " close to "))
    return has_crash and has_hard_brake and has_near


def _is_top_hard_brake_roads_intent(user_query: str) -> bool:
    text = str(user_query or "").strip().lower()
    if not text:
        return False
    normalized = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    has_hard_brake = (
        "hard braking" in text
        or "hard brake" in text
        or "hardbraking" in normalized
        or "hardbrake" in normalized
    )
    has_roads = "road" in text
    has_rank = any(token in text for token in ("top ", " most ", "highest", "rank"))
    return has_hard_brake and has_roads and has_rank


def _extract_top_n(user_query: str, default: int = 5) -> int:
    text = str(user_query or "").strip().lower()
    match = re.search(r"\btop\s+(\d{1,3})\b", text)
    if not match:
        return max(1, min(25, int(default)))
    try:
        return max(1, min(25, int(match.group(1))))
    except (TypeError, ValueError):
        return max(1, min(25, int(default)))


def _extract_hard_brake_group_by(user_query: str) -> str:
    text = str(user_query or "").strip().lower()
    if not text:
        return "segment"
    if any(token in text for token in (" route ref", " by ref", " by route", "highway ref", "road ref")):
        return "ref"
    if any(token in text for token in ("road name", "by name", "by road")):
        return "road_name"
    return "segment"


def _normalize_plan_for_intent(plan: dict[str, Any], user_query: str) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return plan

    normalized = dict(plan)
    steps_raw = normalized.get("steps", [])
    steps: list[dict[str, Any]] = [step for step in steps_raw if isinstance(step, dict)] if isinstance(steps_raw, list) else []
    normalized["steps"] = steps

    domain = str(normalized.get("domain") or "").strip().lower()
    operations = _plan_operations(normalized)
    hard_brake_group_by = _extract_hard_brake_group_by(user_query)

    # Hard braking map-first requests should not force companion head samples
    # unless the user explicitly asked for rows/table samples.
    if domain in {"hard_braking", "hardbraking", "hard_brake", "hardbrake", "hb"}:
        has_map_step = any(
            op in {
                "generate_map",
                "conflation_crashes_near_hard_braking",
                "top_hard_braking_roads",
            }
            for op in operations
        )
        if has_map_step and not _query_explicitly_requests_rows(user_query):
            normalized["steps"] = [
                step
                for step in steps
                if str(step.get("operation") or "").strip().lower() != "head"
            ]

    # Symmetric conflation intent: "crashes near hard braking" and
    # "hard braking near crashes" should both resolve to conflation.
    if _is_crash_hard_brake_near_intent(user_query):
        normalized["domain"] = "hard_braking"
        if "conflation_crashes_near_hard_braking" not in _plan_operations(normalized):
            normalized["steps"] = [
                {
                    "operation": "conflation_crashes_near_hard_braking",
                    "params": {"generate_map": True},
                }
            ]
            prior_reasoning = str(normalized.get("reasoning") or "").strip()
            if prior_reasoning:
                normalized["reasoning"] = (
                    f"{prior_reasoning} Normalized to spatial-temporal conflation for crashes near hard braking."
                )
            else:
                normalized["reasoning"] = "Normalized to spatial-temporal conflation for crashes near hard braking."

    if _is_top_hard_brake_roads_intent(user_query) and not _is_crash_hard_brake_near_intent(user_query):
        normalized["domain"] = "hard_braking"
        if "top_hard_braking_roads" not in _plan_operations(normalized):
            top_n = _extract_top_n(user_query, default=5)
            filter_steps = [
                step
                for step in normalized.get("steps", [])
                if str(step.get("operation") or "").strip().lower() == "filter"
            ]
            normalized["steps"] = filter_steps + [
                {
                    "operation": "top_hard_braking_roads",
                    "params": {
                        "group_by": hard_brake_group_by,
                        "limit": top_n,
                        "hotspot_limit": 1200,
                    },
                }
            ]
            prior_reasoning = str(normalized.get("reasoning") or "").strip()
            if prior_reasoning:
                normalized["reasoning"] = (
                    f"{prior_reasoning} Normalized to top hard-braking roads ranking with scoped map points."
                )
            else:
                normalized["reasoning"] = "Normalized to top hard-braking roads ranking with scoped map points."

    for step in normalized.get("steps", []) or []:
        if str(step.get("operation") or "").strip().lower() != "top_hard_braking_roads":
            continue
        raw_params = step.get("params")
        params_dict = dict(raw_params) if isinstance(raw_params, dict) else {}
        if not params_dict.get("group_by"):
            params_dict["group_by"] = hard_brake_group_by
        step["params"] = params_dict

    return normalized


def _format_row_count(row_count: Any) -> str:
    """Render row count safely even when Postgres returns text values."""
    if row_count in (None, ""):
        return ""
    try:
        if isinstance(row_count, str):
            cleaned = row_count.replace(",", "").strip()
            if not cleaned:
                return ""
            count_value = int(float(cleaned))
        else:
            count_value = int(row_count)
        if count_value <= 0:
            return ""
        return f"  ~{count_value:,} rows"
    except (TypeError, ValueError):
        return f"  ~{row_count} rows"


def _compact_text(value: Any, max_chars: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}... (+{omitted} chars)"


def _build_recent_query_context(max_queries: int = 4) -> str:
    sid = get_active_session()
    if not sid:
        return ""
    events = get_execution_events(sid, limit=60)
    if not events:
        return ""

    plans: list[dict[str, Any]] = [
        row
        for row in events
        if isinstance(row, dict) and str(row.get("type") or "") == "tool_plan"
    ]
    if not plans:
        return ""

    recent_plans = list(reversed(plans[-max(1, int(max_queries)) :]))  # newest first
    lines: list[str] = [
        "[Recent Query History]",
        "Use for follow-up references ('that', 'it', 'break that down').",
        "If current query is explicit, prioritize it over history.",
    ]
    for idx, plan in enumerate(recent_plans, 1):
        lines.append(
            json.dumps(
                {
                    "ref": f"H{idx}",
                    "user_query": _compact_text(plan.get("user_query"), max_chars=180),
                    "dataset": plan.get("dataset"),
                    "dataset_id": plan.get("dataset_id"),
                    "operations": plan.get("operations"),
                },
                ensure_ascii=True,
            )
        )
    return "\n".join(lines)


def _query_needs_polygon_context(user_query: str) -> bool:
    text = str(user_query or "").strip().lower()
    if not text:
        return False
    tokens = (
        "that polygon",
        "this polygon",
        "selected polygon",
        "same polygon",
        "that area",
        "this area",
        "selected area",
        "same area",
        "within that",
        "within this",
        "inside that",
        "inside this",
        "reuse polygon",
    )
    return any(token in text for token in tokens)


def _build_recent_analysis_context(max_entries: int = 4, include_polygon_geometry: bool = False) -> str:
    sid = get_active_session()
    if not sid:
        return ""
    rows = get_analysis_context(sid, limit=max(1, int(max_entries)))
    if not rows:
        return ""
    lines: list[str] = [
        "[Recent Analysis Context]",
        "Use this context for follow-up references to prior crash/workzone/polygon analyses.",
    ]
    geometry_added = False
    for row in reversed(rows):  # newest first
        if not isinstance(row, dict):
            continue
        analysis_type = str(row.get("analysis_type") or "").strip().lower()
        compact = {
            "analysis_type": analysis_type or "unknown",
            "created_at": row.get("created_at"),
            "mode": row.get("mode"),
            "response_summary": _compact_text(row.get("response_summary"), max_chars=400),
            "important_metrics": row.get("important_metrics"),
            "dataset_ids": row.get("dataset_ids"),
            "geometry_meta": row.get("geometry_meta") if analysis_type == "area" else None,
        }
        lines.append(json.dumps(compact, ensure_ascii=True))
        if include_polygon_geometry and analysis_type == "area" and not geometry_added:
            geometry = row.get("geometry")
            if isinstance(geometry, dict):
                geom_json = json.dumps(geometry, separators=(",", ":"), ensure_ascii=True)
                if len(geom_json) <= 20_000:
                    lines.append("[Latest Polygon Geometry]\n" + geom_json)
                    geometry_added = True
    return "\n".join(lines)

# ==========================================
# 0. DATASET CATALOG BUILDER
# ==========================================
def _build_dataset_catalog() -> str:
    """
    Fetches the current session's datasets (including active CV run)
    and formats them as a concise catalog string for the planner prompt.
    """
    try:
        datasets = list_session_datasets()
    except Exception:
        return "  (Dataset catalog unavailable)"

    if not datasets:
        return "  (No datasets loaded in this session)"

    lines = []
    for ds in datasets:
        entity_type = ds.get("entity_type", "unknown")
        dataset_id = ds.get("dataset_id", "")
        row_count = ds.get("row_count")
        count_str = _format_row_count(row_count)

        if entity_type == "cv":
            run_id = ds.get("run_id") or ""
            state_code = ds.get("state_code") or ""
            ts_start = str(ds.get("ts_start") or "")[:10]
            ts_end = str(ds.get("ts_end") or "")[:10]
            active_marker = " [ACTIVE]" if ds.get("is_active") else ""
            label = f"  - CV (traffic){active_marker}: id={run_id or dataset_id}"
            if state_code:
                label += f", state={state_code}"
            if ts_start and ts_end:
                label += f", range={ts_start} to {ts_end}"
            label += count_str
            lines.append(label)
        elif entity_type in ("crash", "workzone", "event", "hard_braking"):
            label = f"  - {entity_type.title()}: id={dataset_id}"
            label += count_str
            lines.append(label)
        else:
            lines.append(f"  - {entity_type}: id={dataset_id}{count_str}")

    return "\n".join(lines)


# ==========================================
# 1. THE PLANNER AGENT (Pure Logic)
# ==========================================
def get_pure_planner_agent() -> LlmAgent:
    """
    Returns the full SQL planner agent with a live dataset catalog
    prepended to its instruction. The catalog is built at call time
    so the planner always sees the current session's loaded datasets.
    """
    catalog = _build_dataset_catalog()
    catalog_prefix = f"""
        ========================
        DATASET CATALOG (this session)
        ========================
        The following datasets are currently loaded and available to query:
{catalog}

        Use these dataset IDs when constructing plans that reference a specific dataset.
        The CV entry marked [ACTIVE] is the default traffic dataset used when no dataset_id is specified.

        """
    agent = get_sql_planner_agent(name="PureSQLPlanner")
    agent.instruction = catalog_prefix + agent.instruction
    return agent

# ==========================================
# 2. THE EXPLAINER AGENT (The Synthesizer)
# ==========================================
def get_explainer_agent() -> LlmAgent:
    """
    Synthesizes the raw database JSON into a natural language summary for the dashboard.
    """
    return LlmAgent(
        name="TitanBotExplainer",
        model=get_agent_model("explainer"),
        description="Translates raw transportation data results into natural language.",
        instruction="""
        You are the final analytical voice of TitanBot, an AI dashboard for transportation data analysis.
        You will receive the user's original query and the raw JSON result of the database execution.
        
        Your job is to translate those raw results into a clear, concise, and helpful natural language summary.

        CRITICAL RULES:
        1. NO TECHNICAL JARGON: Do not mention "JSON", "SQL", "execution plans", or "dictionaries". Speak naturally about the data itself (e.g., "The traffic data shows...").
        2. BE CONCISE: If the result is a long list of roads or crashes, highlight the top 3 to 5 findings and summarize the rest.
        3. HANDLING ZERO RESULTS: If the data is empty or returns 0, do not apologize. Simply state what was searched and politely explain that no records matched those specific filters. Offer one logical adjustment the user could make (e.g., expanding the time window).
        4. HANDLING ERRORS: If the status is "error", briefly explain that there was an issue retrieving that specific data, and suggest a simpler alternative query.
        5. DO NOT HALLUCINATE: Only summarize the data provided in the EXECUTION RESULT. If a metric isn't there, do not invent it.
        """
    )
# ==========================================
# 3. THE PIPELINE ORCHESTRATOR
# ==========================================

def _clean_json_output(output: str) -> str:
    """Strip markdown fencing and repair common LLM JSON mistakes."""
    output = str(output).strip()
    if output.startswith("```json"):
        output = output[7:]
    if output.startswith("```"):
        output = output[3:]
    if output.endswith("```"):
        output = output[:-3]
    output = output.strip()

    # Try to parse as-is first.
    try:
        json.loads(output)
        return output
    except json.JSONDecodeError:
        pass

    # Common mistake: planner puts "notes": "..." as a sibling to step objects
    # inside the "steps" array, producing [..., {"operation":...}, "notes": "...", ...].
    # Strip any non-object entries from the steps array.
    try:
        import re as _re
        parsed = json.loads(_re.sub(r',\s*"notes"\s*:\s*"[^"]*"', '', output))
        return json.dumps(parsed)
    except Exception:
        pass

    return output

async def run_analytical_pipeline(user_query: str) -> str:
    """Executes the full flow: Plan -> Execute -> Explain."""
    logger.info(f"Pipeline: Planning for '{user_query}'")
    
    # 1. Initialize the required memory service
    session_service = InMemorySessionService()
    
    # --- STEP 1: PLAN ---
    planner = get_pure_planner_agent()
    planner_runner = Runner(app_name="TitanBot", agent=planner, session_service=session_service)
    
    planner_session = await session_service.create_session(
        app_name="TitanBot",
        user_id="internal"
    )
    
    planner_input = str(user_query or "").strip()
    recent_query_context = _build_recent_query_context(max_queries=4)
    analysis_context = _build_recent_analysis_context(
        max_entries=4,
        include_polygon_geometry=_query_needs_polygon_context(user_query),
    )
    if recent_query_context:
        planner_input = (
            f"{recent_query_context}\n\n"
            f"[Current Query]\n{planner_input}\n\n"
            "If this is a follow-up reference, inherit matching dataset/domain from history."
        )
    if analysis_context:
        planner_input = f"{analysis_context}\n\n{planner_input}"

    content = types.Content(role='user', parts=[types.Part(text=planner_input)])
    plan_json_str = ""
    planner_usage = new_usage_collector("planner")
    
    # BULLETPROOF TEXT EXTRACTION: Dig into the nested Content -> Parts structure
    try:
        async for event in planner_runner.run_async(user_id="internal", session_id=planner_session.id, new_message=content):
            collect_usage_from_event(planner_usage, event)
            # The ADK uses is_final_response for the final LLM output after tools finish running
            if hasattr(event, "is_final_response") and event.is_final_response():
                for part in _safe_event_parts(event):
                    if hasattr(part, "text") and part.text:
                        plan_json_str += part.text
    finally:
        planner_usage_summary = finalize_usage_collector(planner_usage)
        if planner_usage_summary:
            append_execution_event({"type": "llm_usage", **planner_usage_summary})

    plan_json_str = _clean_json_output(plan_json_str)
    
    # Log the exact JSON string before we try to parse it
    logger.info(f"Raw Planner JSON Output:\n{plan_json_str}")

    # --- CONVERSATIONAL PASS-THROUGH ---
    # If the planner returned plain text (a clarifying question or schema description)
    # instead of a JSON plan, pass it through directly without attempting execution.
    try:
        json.loads(plan_json_str)
        is_json_plan = True
    except (json.JSONDecodeError, ValueError):
        is_json_plan = False

    if not is_json_plan:
        # Planner answered conversationally (e.g., a clarifying question or capability summary).
        # Return it directly — no SQL execution needed.
        logger.info("Planner returned non-JSON (conversational response); passing through.")
        return plan_json_str.strip()

    # --- STEP 2: EXECUTE (Python Only) ---
    execution_result = {}
    try:
        plan = json.loads(plan_json_str)
        plan = _normalize_plan_for_intent(plan, user_query)
        append_execution_event({"type": "pipeline_plan", "plan": plan})
        step_ops = [
            step.get("operation")
            for step in plan.get("steps", [])
            if isinstance(step, dict) and step.get("operation")
        ]
        if not step_ops and plan.get("domain") == "conflation":
            step_ops = ["conflation"]
        append_execution_event(
            {
                "type": "tool_plan",
                "tool": "run_data_analysis",
                "user_query": user_query,
                "dataset": plan.get("domain"),
                "dataset_id": plan.get("dataset_id"),
                "operations": step_ops,
                "query_json": json.dumps(plan, ensure_ascii=True),
            }
        )

        raw_data_str = await asyncio.to_thread(run_unified_sql_query, plan)

        try:
            parsed_data = json.loads(raw_data_str)
        except:
            parsed_data = raw_data_str

        if isinstance(parsed_data, str) and parsed_data.startswith("[QUERYABLE_POLICY_BLOCKED]"):
            message = parsed_data.replace("[QUERYABLE_POLICY_BLOCKED]", "", 1).strip()
            append_execution_event(
                {
                    "type": "tool_result",
                    "tool": "run_data_analysis",
                    "output": json.dumps({"status": "blocked", "message": message}, ensure_ascii=True),
                }
            )
            return message

        execution_result = {"status": "success", "data": parsed_data}
        append_execution_event(
            {
                "type": "tool_result",
                "tool": "run_data_analysis",
                "output": json.dumps(execution_result, ensure_ascii=True),
            }
        )
    except Exception as e:
        logger.error(f"Pipeline Execution Failed: {e}")
        execution_result = {"status": "error", "error": str(e), "bad_plan": plan_json_str}
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "run_data_analysis",
                "error": str(e),
            }
        )

    append_execution_event({"type": "pipeline_result", "result": execution_result})

    # --- STEP 3: EXPLAIN ---
    explainer = get_explainer_agent()
    explainer_runner = Runner(app_name="TitanBot", agent=explainer, session_service=session_service)

    # THE PROPER ADK WAY: Let the service create the explainer session
    explainer_session = await session_service.create_session(
        app_name="TitanBot",
        user_id="internal"
    )

    explanation_context = f"ORIGINAL QUERY: {user_query}\n\nEXECUTION RESULT:\n{json.dumps(execution_result, indent=2)}"
    explain_content = types.Content(role='user', parts=[types.Part(text=explanation_context)])
    explanation_text = ""
    explainer_usage = new_usage_collector("explainer")

    # Pass the correctly generated explainer_session.id
    try:
        async for event in explainer_runner.run_async(user_id="internal", session_id=explainer_session.id, new_message=explain_content):
            collect_usage_from_event(explainer_usage, event)
            if hasattr(event, "is_final_response") and event.is_final_response():
                for part in _safe_event_parts(event):
                    text = getattr(part, "text", None)
                    if text:
                        explanation_text = text
                        break
    finally:
        explainer_usage_summary = finalize_usage_collector(explainer_usage)
        if explainer_usage_summary:
            append_execution_event({"type": "llm_usage", **explainer_usage_summary})

    return explanation_text.strip()


# ==========================================
# 4. TOOL EXPORT
# ==========================================
def get_pipeline_tool() -> FunctionTool:
    """
    Wraps the pipeline as a tool for the Main Agent.
    """
    run_analytical_pipeline.__name__ = "run_data_analysis"
    return FunctionTool(run_analytical_pipeline)

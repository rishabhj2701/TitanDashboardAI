# dynamic_analyst/pipeline_react.py
"""
ReAct-based pipeline for TitanBot.

Replaces the Plan → Execute → Explain waterfall with a single agent that
calls tools directly and observes each result before deciding next steps.

The agent loop:
  1. Reason about what's needed
  2. Call a tool (list_datasets / get_schema / execute_query / run_conflation)
  3. Observe the result
  4. Repeat until the answer is ready, then reply

Benefits over the waterfall:
  - Zero rows? The agent sees it and widens filters on the next call.
  - Multi-step questions chain naturally without pre-planning.
  - No _normalize_plan_for_intent regex heuristics needed.
  - No separate explainer agent — the agent synthesises inline.
  - Bad schema reference? get_schema catches it before SQL runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

from .config import AGENT_SKILLS_ENABLED, AGENT_SKILLS_MAX_ATTEMPTS_PER_TURN
from .modeling import collect_usage_from_event, finalize_usage_collector, get_agent_model, new_usage_collector
from .intent_routing import (
    build_avg_speed_near_steps,
    build_avg_speed_range_steps,
    build_crash_avg_speed_range_steps,
    build_crash_corridor_datetime_steps,
    build_crash_count_steps,
    build_crash_speed_limit_steps,
    build_crash_time_window_steps,
    build_named_road_steps,
    build_show_all_hard_braking_steps,
    build_sparse_crash_interval_steps,
    build_speed_limit_range_steps,
    build_speed_limit_roads_steps,
    build_top_crash_segments_steps,
    is_avg_speed_near_intent,
    is_avg_speed_range_intent,
    is_crash_avg_speed_range_intent,
    is_crash_corridor_datetime_intent,
    is_crash_count_intent,
    is_crash_speed_limit_intent,
    is_crash_time_window_intent,
    is_show_all_hard_braking_intent,
    is_show_named_road_intent,
    is_sparse_crash_interval_intent,
    is_speed_limit_range_intent,
    is_speed_limit_roads_intent,
    is_top_crash_segments_intent,
    normalize_execute_query_plan,
    query_requests_map,
    sanitize_query_text,
)
from .orchestration.sql_dispatcher import run_unified_sql_query, supported_sql_domains
from .pipeline import _extract_top_n, _is_top_hard_brake_roads_intent
from .policy_controller import choose_direct_skill_route as choose_policy_skill_route
from .policy_controller import decide_policy
from .policy_controller import run_direct_policy_route
from .session_state import (
    append_execution_event,
    get_active_session,
    get_analysis_context,
    get_execution_events,
    get_last_map,
    get_scoped_session_key,
)
from .services.datasets import _latest_event_dataset_id, list_session_datasets
from .skills.executor import execute_skill as execute_structured_skill
from .skills.executor import list_available_skills

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema cache (extracted to caching.py; re-exported for backward compat)
# ---------------------------------------------------------------------------
from .caching import (
    _SCHEMA_CACHE,
    _CATALOG_CACHE,
    _MAX_SCHEMA_CACHE_ENTRIES,
    _SCHEMA_CACHE_TTL_SECONDS,
    _CATALOG_TTL_SECONDS,
    _schema_cache_key,
    _store_schema,
    clear_schema_cache_for_session,  # re-exported for chat.py, uploads.py, dataset_mappings.py
)

_MAX_EXECUTE_QUERY_ATTEMPTS_PER_TURN = 2
_MAX_RUN_CONFLATION_ATTEMPTS_PER_TURN = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_event_parts(event: Any) -> list[Any]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    return parts if isinstance(parts, list) else list(parts)


def _compact_text(value: Any, max_chars: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... (+{len(text) - max_chars} chars)"


def _choose_direct_skill_route(user_query: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    return choose_policy_skill_route(user_query)


async def _run_direct_skill_route(user_query: str) -> str | None:
    return await run_direct_policy_route(user_query)


def _extract_route_refs(text: str, max_items: int = 4) -> list[str]:
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b(i|us|mo|sr|route)\s*[- ]?\s*(\d{1,3})\b", raw, flags=re.IGNORECASE):
        prefix = match.group(1).upper()
        number = match.group(2)
        if prefix == "ROUTE":
            prefix = "MO"
        ref = f"{prefix}-{number}"
        if ref in seen:
            continue
        seen.add(ref)
        found.append(ref)
        if len(found) >= max(1, int(max_items)):
            break
    return found


def _active_road_scope() -> dict[str, Any]:
    sid = get_active_session()
    if not sid:
        return {}
    last_map = get_last_map(sid)
    if not isinstance(last_map, dict):
        return {}
    road_filter = last_map.get("roadAggregateFilter")
    if not isinstance(road_filter, dict) or not road_filter:
        return {}
    return {
        "label": _road_scope_label(road_filter),
        "roadAggregateFilter": dict(road_filter),
        "analysis_type": last_map.get("analysis_type"),
    }


def _road_scope_label(road_filter: dict[str, Any]) -> str:
    if not isinstance(road_filter, dict):
        return ""

    def _clean_list(value: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(value, list):
            return out
        seen: set[str] = set()
        for raw in value:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    road_names = _clean_list(road_filter.get("road_names"))
    if road_names:
        label = ", ".join(road_names[:3])
        if len(road_names) > 3:
            label += f" (+{len(road_names) - 3} more)"
        return label

    road_refs = _clean_list(road_filter.get("road_refs"))
    if road_refs:
        label = ", ".join(road_refs[:3])
        if len(road_refs) > 3:
            label += f" (+{len(road_refs) - 3} more)"
        return label

    for key in ("road_name", "road_segment_id"):
        value = str(road_filter.get(key) or "").strip()
        if value:
            return value
    return "active road scope"


def _build_road_scope_filter_conditions(road_filter: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(road_filter, dict):
        return []

    conditions: list[dict[str, Any]] = []

    road_names = road_filter.get("road_names")
    if isinstance(road_names, list):
        cleaned = [str(v).strip() for v in road_names if str(v or "").strip()]
        if cleaned:
            conditions.append({"column": "road_name", "operator": "in", "value": cleaned[:40]})

    road_refs = road_filter.get("road_refs")
    if isinstance(road_refs, list):
        cleaned_refs = [str(v).strip() for v in road_refs if str(v or "").strip()]
        if cleaned_refs:
            conditions.append({"column": "road_ref", "operator": "in", "value": cleaned_refs[:40]})

    road_segment_ids = road_filter.get("road_segment_ids")
    if isinstance(road_segment_ids, list):
        cleaned_segments = [str(v).strip() for v in road_segment_ids if str(v or "").strip()]
        if cleaned_segments:
            conditions.append({"column": "road_segment_id", "operator": "in", "value": cleaned_segments[:40]})

    road_name = str(road_filter.get("road_name") or "").strip()
    if road_name:
        pattern = road_name if "%" in road_name else f"%{road_name}%"
        conditions.append({"column": "road_name", "operator": "ilike", "value": pattern})

    road_segment_id = str(road_filter.get("road_segment_id") or "").strip()
    if road_segment_id:
        conditions.append({"column": "road_segment_id", "operator": "=", "value": road_segment_id})

    return conditions


def _build_route_ref_filter_conditions(route_refs: list[str]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for ref in route_refs[:4]:
        search_term = str(ref or "").strip().replace("-", " ")
        if not search_term:
            continue
        conditions.append({"column": "road_name", "operator": "ilike", "value": f"%{search_term}%"})
    return conditions


def _is_same_road_followup(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(
        token in lowered
        for token in (
            "same roads",
            "same road",
            "same corridor",
            "keep the same roads",
            "keep the same road",
            "those roads",
            "that corridor",
            "same route",
        )
    )


def _is_ambiguous_incident_corridor_prompt(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered or not _extract_route_refs(lowered):
        return False
    if "incident" not in lowered:
        return False
    traffic_anchor_tokens = (
        "traffic",
        "road network",
        "vehicle",
        "speed",
        "congestion",
        "travel time",
    )
    return not any(token in lowered for token in traffic_anchor_tokens)


def _normalize_followup_user_query(user_query: str) -> str:
    raw = str(user_query or "").strip()
    if not raw or _extract_route_refs(raw):
        return raw

    active_scope = _active_road_scope()
    if not active_scope or not _is_same_road_followup(raw):
        return raw

    label = str(active_scope.get("label") or "").strip()
    if not label:
        return raw
    return f"On {label}, {raw}"


def _steps_include_explicit_road_scope(steps: list[dict[str, Any]]) -> bool:
    road_cols = {"road", "road_name", "name", "road_segment_id", "road_ref", "ref", "highway"}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "").strip().lower() != "filter":
            continue
        params = step.get("params") or {}
        conditions = params.get("conditions") or []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            column = str(cond.get("column") or "").strip().lower()
            if column in road_cols:
                return True
    return False


def _looks_like_scoped_top_segment_speed_followup(reasoning: str, steps: list[dict[str, Any]]) -> bool:
    lowered = str(reasoning or "").strip().lower()
    segment_hint = "segment" in lowered
    speed_hint = "speed" in lowered and ("top" in lowered or "fastest" in lowered or "highest" in lowered)

    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("operation") or "").strip().lower()
        params = step.get("params") or {}
        if op == "groupby":
            group_by = params.get("group_by") or []
            if isinstance(group_by, list) and "road_segment_id" in group_by:
                segment_hint = True
            aggs = params.get("aggregations") or {}
            agg_text = json.dumps(aggs, ensure_ascii=True).lower()
            if "speed" in agg_text:
                speed_hint = True
        elif op == "sort":
            sort_by = str(params.get("sort_by") or params.get("column") or "").strip().lower()
            if "speed" in sort_by:
                speed_hint = True
        elif op == "top_speed_roads":
            segment_hint = True
            speed_hint = True

    return segment_hint and speed_hint


def _rewrite_scoped_top_segment_speed_steps(
    steps: list[dict[str, Any]],
    *,
    scope_conditions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not scope_conditions:
        return steps

    limit_n = 10
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "").strip().lower() == "head":
            params = step.get("params") or {}
            try:
                limit_n = max(1, min(25, int(params.get("n", params.get("count", limit_n)))))
            except Exception:
                limit_n = 10
            break

    return [
        {"operation": "filter", "params": {"mode": "and", "conditions": scope_conditions}},
        {
            "operation": "groupby",
            "params": {
                "group_by": ["road_segment_id"],
                "aggregations": {
                    "road_name": {"fn": "max", "column": "road_name"},
                    "avg_speed_mph": {"fn": "avg", "column": "speed"},
                    "point_count": "count",
                },
            },
        },
        {"operation": "sort", "params": {"sort_by": "avg_speed_mph", "order": "desc"}},
        {"operation": "head", "params": {"n": limit_n}},
    ]


def _current_turn_events(limit: int = 80) -> list[dict[str, Any]]:
    sid = get_active_session()
    if not sid:
        return []
    events = get_execution_events(sid, limit=limit)
    turn_start_idx = -1
    for idx, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("type") or "") == "react_turn_start":
            turn_start_idx = idx
    if turn_start_idx < 0:
        return [event for event in events if isinstance(event, dict)]
    return [event for event in events[turn_start_idx + 1 :] if isinstance(event, dict)]


def _tool_attempt_limit(tool_name: str) -> int:
    if tool_name == "run_skill":
        return max(1, int(AGENT_SKILLS_MAX_ATTEMPTS_PER_TURN))
    if tool_name == "execute_query":
        return _MAX_EXECUTE_QUERY_ATTEMPTS_PER_TURN
    if tool_name == "run_conflation":
        return _MAX_RUN_CONFLATION_ATTEMPTS_PER_TURN
    return 1


def _classify_tool_error(error_text: Any) -> str:
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return "execution_error"
    if "tool budget" in lowered or "attempt budget" in lowered:
        return "tool_budget_exceeded"
    if "queryable_policy_blocked" in lowered:
        return "queryable_policy_blocked"
    if "unsupported column" in lowered:
        return "unsupported_column"
    if "syntax error" in lowered:
        return "syntax_error"
    if "statement timeout" in lowered or "timed out" in lowered or "querycanceled" in lowered:
        return "timeout"
    if "must be valid json" in lowered or "must decode to a json object" in lowered:
        return "invalid_tool_input"
    return "execution_error"


def _tool_failure_guidance(tool_name: str, error_type: str) -> str:
    if tool_name == "execute_query":
        if error_type == "timeout":
            return "This query path timed out. Narrow the scope, ask a focused clarification, or switch to a structured skill."
        if error_type == "unsupported_column":
            return "A prior query used a column that is not available. Call get_schema again before another execute_query attempt."
        if error_type == "syntax_error":
            return "The prior query shape was invalid. Rebuild the plan with simpler operations and safe aliases before retrying."
        if error_type == "tool_budget_exceeded":
            return "The execute_query attempt budget for this turn is exhausted. Stop retrying broad SQL plans and either answer with the best available result or ask for a narrower request."
    if tool_name == "run_skill":
        if error_type == "tool_budget_exceeded":
            return "The run_skill attempt budget for this turn is exhausted. Do not try another skill call in this turn."
        if error_type in {"invalid_tool_input", "execution_error"}:
            return "The skill call failed or lacked valid inputs. Ask a focused clarification or switch to generic tools."
    if tool_name == "run_conflation" and error_type == "tool_budget_exceeded":
        return "The run_conflation attempt budget for this turn is exhausted. Do not retry the same cross-domain join in this turn."
    return "This tool call failed. Adjust the plan before retrying."


def _tool_attempt_budget_error(tool_name: str) -> dict[str, Any] | None:
    turn_events = _current_turn_events()
    attempts = sum(
        1
        for event in turn_events
        if str(event.get("type") or "") == "tool_plan" and str(event.get("tool") or "") == tool_name
    )
    limit = _tool_attempt_limit(tool_name)
    if attempts < limit:
        return None
    recent_errors = [
        str(event.get("error_type") or _classify_tool_error(event.get("error")))
        for event in turn_events
        if str(event.get("type") or "") == "tool_error" and str(event.get("tool") or "") == tool_name
    ]
    recent_error_types = [err for err in recent_errors if err]
    guidance = _tool_failure_guidance(tool_name, "tool_budget_exceeded")
    return {
        "status": "error",
        "error_type": "tool_budget_exceeded",
        "error": f"{tool_name} exceeded the per-turn attempt budget ({limit}).",
        "message": guidance,
        "tool": tool_name,
        "attempts": attempts,
        "attempt_limit": limit,
        "recent_error_types": recent_error_types[-3:],
    }


def _policy_guidance_block(decision: Any | None) -> str:
    candidates = list(getattr(decision, "candidate_skills", ()) or ())
    if not AGENT_SKILLS_ENABLED or not candidates:
        return ""
    lines = ["[Policy Guidance]"]
    primary = candidates[0]
    skill_id = str(primary.get("skill_id") or "").strip()
    reason = str(primary.get("reason") or "").strip()
    if skill_id:
        lines.append(f"Preferred skill candidate for this request: {skill_id}.")
    if reason:
        lines.append(f"Reason: {reason}.")
    lines.append("Try at most one run_skill call in this turn.")
    lines.append("If a skill fails or inputs are unclear, switch to generic tools or ask a focused clarification.")
    lines.append("Do not keep retrying execute_query after repeated timeout, syntax, or unsupported-column errors.")
    return "\n".join(lines)


def _build_context_block(max_entries: int = 4) -> str:
    """Build recent query + analysis context for the agent's system message."""
    sid = get_active_session()
    if not sid:
        return ""

    lines: list[str] = []

    active_scope = _active_road_scope()
    if active_scope:
        lines.append("[Active Road Scope — use for road/corridor follow-ups like 'same roads']")
        lines.append(json.dumps(active_scope, ensure_ascii=True))

    # Recent tool calls (last few queries)
    events = get_execution_events(sid, limit=40)
    plans = [e for e in events if isinstance(e, dict) and e.get("type") == "tool_plan"]
    if plans:
        lines.append("[Recent Queries — use for follow-up references like 'that', 'those roads']")
        for i, p in enumerate(reversed(plans[-max_entries:]), 1):
            lines.append(json.dumps({
                "ref": f"Q{i}",
                "user_query": _compact_text(p.get("user_query"), 160),
                "domain": p.get("dataset"),
                "dataset_id": p.get("dataset_id"),
                "ops": p.get("operations"),
            }, ensure_ascii=True))

    # Recent analysis context (polygons, prior crash analyses, etc.)
    analysis = get_analysis_context(sid, limit=max_entries)
    if analysis:
        lines.append("[Prior Analysis Context — use for polygon/area follow-ups]")
        for row in reversed(analysis):
            if not isinstance(row, dict):
                continue
            lines.append(json.dumps({
                "type": row.get("analysis_type"),
                "summary": _compact_text(row.get("response_summary"), 300),
                "metrics": row.get("important_metrics"),
                "dataset_ids": row.get("dataset_ids"),
                "geometry_meta": row.get("geometry_meta") if row.get("analysis_type") == "area" else None,
            }, ensure_ascii=True))

    return "\n".join(lines)


def _build_dataset_catalog() -> str:
    scoped = get_scoped_session_key() or "__nosession__"
    cached = _CATALOG_CACHE.get(scoped)
    if cached and (time.monotonic() - cached[0]) < _CATALOG_TTL_SECONDS:
        return cached[1]

    try:
        datasets = list_session_datasets()
    except Exception:
        return "  (Dataset catalog unavailable)"
    if not datasets:
        return "  (No datasets loaded in this session)"

    lines = []
    for ds in datasets:
        etype = ds.get("entity_type", "unknown")
        did = ds.get("dataset_id", "")
        count = ds.get("row_count")
        count_str = f"  ~{int(count):,} rows" if count else ""
        if etype == "cv":
            run_id = ds.get("run_id") or did
            active = " [ACTIVE]" if ds.get("is_active") else ""
            ts_start = str(ds.get("ts_start") or "")[:10]
            ts_end = str(ds.get("ts_end") or "")[:10]
            label = f"  - CV (traffic){active}: id={run_id}"
            if ds.get("state_code"):
                label += f", state={ds['state_code']}"
            if ts_start and ts_end:
                label += f", range={ts_start} to {ts_end}"
            lines.append(label + count_str)
        else:
            lines.append(f"  - {etype.title()}: id={did}{count_str}")
    result = "\n".join(lines)
    _CATALOG_CACHE[scoped] = (time.monotonic(), result)
    return result


# ---------------------------------------------------------------------------
# Tool 1: list_datasets
# ---------------------------------------------------------------------------

async def list_datasets() -> str:
    """
    List all datasets currently loaded in this session, including their IDs,
    types (traffic/crash/workzone/hard_braking), row counts, and date ranges.

    Call this first when the user asks what data is available, or when you
    need a dataset_id to pass to execute_query.
    """
    return _build_dataset_catalog()


# ---------------------------------------------------------------------------
# Tool 2: get_schema
# ---------------------------------------------------------------------------

async def get_schema(domain: str, dataset_id: str | None = None) -> str:
    """
    Return the schema for a domain: available columns, groupable columns,
    aggregate columns, and supported operations.

    Always call this before execute_query to confirm column names are valid.
    If the user references a column you're unsure about, check here first.

    Args:
        domain: One of: traffic, hard_braking, crash, workzone, conflation
        dataset_id: Optional — the specific dataset ID to inspect. Leave blank
                    to use the active/latest dataset for that domain.
    """
    domain = str(domain or "").strip().lower()
    if not domain:
        return json.dumps({"error": "domain is required"})

    cache_key = _schema_cache_key(domain, dataset_id)
    cached = _SCHEMA_CACHE.get(cache_key)
    if cached is not None and (time.monotonic() - cached[0]) < _SCHEMA_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        if domain == "traffic":
            from .sql.traffic import get_traffic_domain_schema
            schema = await asyncio.to_thread(get_traffic_domain_schema, dataset_id)
            result = json.dumps(schema, default=str)

        elif domain in {"hard_braking", "hard-braking", "hardbraking", "hb"}:
            from .sql.traffic import get_hard_braking_domain_schema
            schema = await asyncio.to_thread(get_hard_braking_domain_schema, dataset_id)
            result = json.dumps(schema, default=str)

        elif domain == "crash":
            from .sql.crash import get_crash_domain_schema
            schema = await asyncio.to_thread(get_crash_domain_schema, dataset_id)
            schema["queryable_policy_note"] = (
                "Only columns enabled in the Ingestion Inspector are listed above. "
                "If the user wants a column not shown, they can enable it there."
            )
            result = json.dumps(schema, default=str)

        elif domain == "workzone":
            from .sql.workzone import get_workzone_domain_schema
            schema = await asyncio.to_thread(get_workzone_domain_schema, dataset_id)
            schema["queryable_policy_note"] = (
                "Only columns enabled in the Ingestion Inspector are listed above. "
                "If the user wants a column not shown, they can enable it there."
            )
            result = json.dumps(schema, default=str)

        elif domain == "conflation":
            result = json.dumps({
                "domain": "conflation",
                "description": "Cross-domain spatial-temporal join between two or more datasets.",
                "note": "Use run_conflation() directly — this domain has no step-based operations.",
            })

        else:
            return json.dumps({"error": f"Unknown domain '{domain}'. Supported: {', '.join(supported_sql_domains())}"})

        _store_schema(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"get_schema failed: {e}")
        append_execution_event({"type": "tool_error", "tool": "get_schema", "error": str(e)})
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool 3: execute_query
# ---------------------------------------------------------------------------

async def execute_query(
    domain: str,
    steps_json: str,
    dataset_id: str | None = None,
    reasoning: str | None = None,
) -> str:
    """
    Execute a query against a single dataset domain and return the result.

    OBSERVE the result before replying — if row_count is 0 or data is empty,
    adjust your filters and call again with looser constraints.

    Args:
        domain: One of: traffic, hard_braking, crash, workzone
        steps_json: JSON array string of operation steps. Each step is:
               {"operation": "<op_name>", "params": {...}}

               Supported operations:
               - filter:        {"mode": "and"|"or", "conditions": [{"column": "x", "operator": "=", "value": "y"}]}
               - having:        {"mode": "and"|"or", "conditions": [...]}  (post-aggregation filter)
               - groupby:       {"group_by": ["col1"], "aggregations": {"alias": {"fn": "mean", "column": "col"}}}
               - aggregate:     {"aggregations": {"alias": "mean(col)"}}
               - unique_values: {"column": "col_name"}
               - sort:          {"sort_by": "col", "order": "asc"|"desc"}
               - head:          {"n": 10}
               - generate_map:  {"map_type": "points", "label_column": "road_name", "limit": 2000}
               - top_speed_roads: {}  (traffic only)
               - top_hard_braking_roads: {"limit": 5, "group_by": "segment"}  (hard_braking only)
               - conflation_crashes_near_hard_braking: {"distance_m": 200, "time_window_minutes": 60}  (hard_braking only)

               Example: '[{"operation":"generate_map","params":{"map_type":"points","limit":2000}}]'

        dataset_id: Optional specific dataset ID. Omit to use the active dataset.
        reasoning: Optional short explanation of why you're running this query.

    Returns:
        JSON string with the query result. Key fields to check:
        - "row_count" or "count": how many rows matched
        - "data": the actual rows/aggregations
        - "status": "success" or "error"
        - "map_generated": true if a map was saved for this session
    """
    domain = str(domain or "").strip().lower()
    if not domain:
        return json.dumps({"status": "error", "error": "domain is required"})

    budget_error = _tool_attempt_budget_error("execute_query")
    if budget_error is not None:
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "execute_query",
                "error": budget_error["error"],
                "error_type": budget_error["error_type"],
            }
        )
        return json.dumps(budget_error, ensure_ascii=True)

    try:
        steps = json.loads(steps_json)
    except Exception:
        return json.dumps({"status": "error", "error": f"steps_json must be a valid JSON array. Got: {steps_json[:200]}"})

    if not isinstance(steps, list) or not steps:
        return json.dumps({"status": "error", "error": "steps_json must be a non-empty JSON array"})

    effective_reasoning = sanitize_query_text(reasoning or "")
    if _is_top_hard_brake_roads_intent(effective_reasoning):
        top_n = _extract_top_n(effective_reasoning, default=5)
        domain = "traffic"
        steps = [
            {
                "operation": "groupby",
                "params": {
                    "group_by": ["label"],
                    "aggregations": {
                        "hard_brake_count": {
                            "fn": "sum",
                            "column": "hard_brake_count",
                            "alias": "hard_brake_count",
                        }
                    },
                },
            },
            {"operation": "sort", "params": {"sort_by": "hard_brake_count", "order": "desc"}},
            {"operation": "head", "params": {"n": top_n}},
            {"operation": "generate_map", "params": {"limit": top_n}},
        ]
    elif is_top_crash_segments_intent(effective_reasoning):
        domain = "crash"
        steps = build_top_crash_segments_steps(effective_reasoning)
    elif is_sparse_crash_interval_intent(effective_reasoning):
        domain = "crash"
        steps = build_sparse_crash_interval_steps(effective_reasoning)
    elif is_crash_count_intent(effective_reasoning):
        domain = "crash"
        steps = build_crash_count_steps(
            effective_reasoning,
            with_map=query_requests_map(effective_reasoning),
        )
    elif is_crash_speed_limit_intent(effective_reasoning):
        domain = "crash"
        steps = build_crash_speed_limit_steps(effective_reasoning)
    elif is_crash_avg_speed_range_intent(effective_reasoning):
        domain = "crash"
        steps = build_crash_avg_speed_range_steps(effective_reasoning)
    elif is_avg_speed_near_intent(effective_reasoning):
        domain = "traffic"
        steps = build_avg_speed_near_steps(effective_reasoning)
    elif is_avg_speed_range_intent(effective_reasoning):
        domain = "traffic"
        steps = build_avg_speed_range_steps(effective_reasoning)
    elif is_speed_limit_range_intent(effective_reasoning):
        domain = "traffic"
        steps = build_speed_limit_range_steps(effective_reasoning)
    elif is_speed_limit_roads_intent(effective_reasoning):
        domain = "traffic"
        steps = build_speed_limit_roads_steps(effective_reasoning)
    elif is_crash_corridor_datetime_intent(effective_reasoning):
        domain = "crash"
        steps = build_crash_corridor_datetime_steps(effective_reasoning)
    elif is_crash_time_window_intent(effective_reasoning):
        domain = "crash"
        steps = build_crash_time_window_steps(effective_reasoning)
    elif is_show_all_hard_braking_intent(effective_reasoning):
        domain = "traffic"
        steps = build_show_all_hard_braking_steps(limit=50)
    elif is_show_named_road_intent(effective_reasoning):
        domain = "traffic"
        steps = build_named_road_steps(effective_reasoning)
    else:
        resolved_dataset_id = dataset_id
        if not resolved_dataset_id and str(domain or "").strip().lower() in {"crash", "crashes"}:
            try:
                resolved_dataset_id = _latest_event_dataset_id(entity_type="crash")
            except Exception:
                resolved_dataset_id = dataset_id
        domain, steps, dataset_id = normalize_execute_query_plan(
            domain,
            steps,
            effective_reasoning,
            dataset_id=resolved_dataset_id,
        )

    if domain == "traffic":
        active_scope = _active_road_scope()
        road_filter = active_scope.get("roadAggregateFilter") if active_scope else None
        route_refs = _extract_route_refs(effective_reasoning)
        scope_conditions: list[dict[str, Any]] = []
        scope_label = ""
        if len(route_refs) == 1:
            scope_conditions = _build_route_ref_filter_conditions(route_refs)
            scope_label = route_refs[0]
        elif isinstance(road_filter, dict) and road_filter:
            scope_conditions = _build_road_scope_filter_conditions(road_filter)
            scope_label = str(active_scope.get("label") or "").strip()
        if (
            scope_conditions
            and not _steps_include_explicit_road_scope(steps)
            and _looks_like_scoped_top_segment_speed_followup(effective_reasoning, steps)
        ):
            steps = _rewrite_scoped_top_segment_speed_steps(steps, scope_conditions=scope_conditions)
            if scope_label:
                effective_reasoning = f"{effective_reasoning}\nActive road scope: {scope_label}".strip()

    plan: dict[str, Any] = {
        "domain": domain,
        "steps": steps,
        "reasoning": effective_reasoning,
    }
    if dataset_id:
        plan["dataset_id"] = dataset_id

    ops = [s.get("operation") for s in steps if isinstance(s, dict) and s.get("operation")]
    append_execution_event({
        "type": "tool_plan",
        "tool": "execute_query",
        "user_query": effective_reasoning,
        "dataset": domain,
        "dataset_id": dataset_id,
        "operations": ops,
        "query_json": json.dumps(plan, ensure_ascii=True),
    })

    try:
        raw = await asyncio.to_thread(run_unified_sql_query, plan)

        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw

        # Detect queryable policy block (domain runners return a prefixed string, not JSON)
        if isinstance(parsed, str) and parsed.startswith("[QUERYABLE_POLICY_BLOCKED]"):
            guidance = parsed.removeprefix("[QUERYABLE_POLICY_BLOCKED]\n").strip()
            err = {"status": "error", "error_type": "queryable_policy_blocked", "message": guidance}
            append_execution_event({"type": "tool_error", "tool": "execute_query", "error": guidance})
            return json.dumps(err, ensure_ascii=True)

        if isinstance(parsed, dict) and (
            str(parsed.get("status") or "").strip().lower() == "error" or parsed.get("error")
        ):
            error_text = str(parsed.get("error") or parsed.get("message") or "Query execution failed")
            error_type = str(parsed.get("error_type") or _classify_tool_error(error_text))
            guidance = str(parsed.get("message") or _tool_failure_guidance("execute_query", error_type))
            err = dict(parsed)
            err["status"] = "error"
            err["error"] = error_text
            err["error_type"] = error_type
            err["message"] = guidance
            append_execution_event(
                {
                    "type": "tool_error",
                    "tool": "execute_query",
                    "error": error_text,
                    "error_type": error_type,
                }
            )
            return json.dumps(err, ensure_ascii=True, default=str)

        result = {"status": "success", "data": parsed}
        append_execution_event({
            "type": "tool_result",
            "tool": "execute_query",
            "output": json.dumps(result, ensure_ascii=True),
        })
        return json.dumps(result, ensure_ascii=True, default=str)

    except Exception as e:
        logger.error(f"execute_query failed: {e}")
        error_type = _classify_tool_error(str(e))
        err = {
            "status": "error",
            "error": str(e),
            "error_type": error_type,
            "message": _tool_failure_guidance("execute_query", error_type),
            "domain": domain,
            "ops": ops,
        }
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "execute_query",
                "error": str(e),
                "error_type": error_type,
            }
        )
        return json.dumps(err, ensure_ascii=True)


# ---------------------------------------------------------------------------
# Tool 4: run_conflation
# ---------------------------------------------------------------------------

async def run_conflation(
    datasets: list[str],
    max_distance_meters: float = 500.0,
    time_mode: str = "auto",
    time_window_minutes: int = 60,
    generate_map: bool = True,
    limit: int = 5000,
) -> str:
    """
    Run a spatial-temporal cross-domain join between two or more datasets.

    Use this when the user asks about relationships BETWEEN data types, e.g.:
    - "crashes near workzones"
    - "hard braking near crash locations"
    - "traffic speed near accidents"
    - "are there more crashes in construction zones?"

    Args:
        datasets: List of 2+ dataset names. Use: "crashes", "workzones",
                  "traffic", "hard_braking"
        max_distance_meters: Spatial match radius (default 500, max 2000)
        time_mode: "auto" (detect best mode), "window" (time buffer), 
                   "overlap" (date range overlap), "none" (spatial only)
        time_window_minutes: Time buffer for window mode (default 60)
        generate_map: Whether to generate a map overlay (default True)
        limit: Max matched pairs to return (default 5000)

    Returns:
        JSON string with match counts, average distances, time deltas,
        and a map overlay if generate_map=True.
    """
    if not datasets or len(datasets) < 2:
        return json.dumps({"status": "error", "error": "datasets must contain at least 2 entries"})

    budget_error = _tool_attempt_budget_error("run_conflation")
    if budget_error is not None:
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "run_conflation",
                "error": budget_error["error"],
                "error_type": budget_error["error_type"],
            }
        )
        return json.dumps(budget_error, ensure_ascii=True)

    plan: dict[str, Any] = {
        "domain": "conflation",
        "datasets": datasets,
        "max_distance_meters": float(max_distance_meters),
        "time_mode": str(time_mode),
        "time_window_minutes": int(time_window_minutes),
        "generate_map": bool(generate_map),
        "limit": int(limit),
    }

    append_execution_event({
        "type": "tool_plan",
        "tool": "run_conflation",
        "dataset": "conflation",
        "operations": ["conflation"],
        "query_json": json.dumps(plan, ensure_ascii=True),
    })
    try:
        raw = await asyncio.to_thread(run_unified_sql_query, plan)

        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw

        result = {"status": "success", "data": parsed}
        append_execution_event({
            "type": "tool_result",
            "tool": "run_conflation",
            "output": json.dumps(result, ensure_ascii=True),
        })
        return json.dumps(result, ensure_ascii=True, default=str)

    except Exception as e:
        logger.error(f"run_conflation failed: {e}")
        error_type = _classify_tool_error(str(e))
        err = {
            "status": "error",
            "error": str(e),
            "error_type": error_type,
            "message": _tool_failure_guidance("run_conflation", error_type),
            "datasets": datasets,
        }
        append_execution_event({
            "type": "tool_error",
            "tool": "run_conflation",
            "error": str(e),
            "error_type": error_type,
        })
        return json.dumps(err, ensure_ascii=True)


# ---------------------------------------------------------------------------
# Tool 5: list_skills
# ---------------------------------------------------------------------------

async def list_skills() -> str:
    """
    List structured skills that can be used for common intents.
    Returns a JSON payload with skill metadata and required inputs.
    """
    if not AGENT_SKILLS_ENABLED:
        return json.dumps(
            {
                "status": "disabled",
                "skills_enabled": False,
                "message": "Structured skills are disabled.",
                "skills": [],
            },
            ensure_ascii=True,
        )
    skills = list_available_skills()
    return json.dumps(
        {
            "status": "success",
            "skills_enabled": True,
            "skills": skills,
        },
        ensure_ascii=True,
        default=str,
    )


# ---------------------------------------------------------------------------
# Tool 6: run_skill
# ---------------------------------------------------------------------------

async def run_skill(skill_id: str, args_json: str = "{}", user_query: str = "") -> str:
    """
    Execute a structured skill by ID.

    Args:
        skill_id: Registry skill identifier (e.g., road_corridor_map_scope).
        args_json: JSON object string for required/optional skill inputs.
        user_query: Original user query for intent ranking context.
    """
    sid = str(skill_id or "").strip()
    if not sid:
        return json.dumps({"status": "error", "error": "skill_id is required"}, ensure_ascii=True)

    budget_error = _tool_attempt_budget_error("run_skill")
    if budget_error is not None:
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "run_skill",
                "error": budget_error["error"],
                "error_type": budget_error["error_type"],
            }
        )
        return json.dumps(budget_error, ensure_ascii=True)

    try:
        args = json.loads(args_json or "{}")
    except Exception:
        return json.dumps({"status": "error", "error": "args_json must be valid JSON object"}, ensure_ascii=True)
    if not isinstance(args, dict):
        return json.dumps({"status": "error", "error": "args_json must decode to a JSON object"}, ensure_ascii=True)

    append_execution_event(
        {
            "type": "tool_plan",
            "tool": "run_skill",
            "dataset": "skill",
            "operations": [sid],
            "user_query": user_query or "",
            "query_json": json.dumps({"skill_id": sid, "args": args}, ensure_ascii=True),
        }
    )
    try:
        result = await asyncio.to_thread(
            execute_structured_skill,
            skill_id=sid,
            args=args,
            user_query=user_query or "",
        )
        append_execution_event(
            {
                "type": "tool_result",
                "tool": "run_skill",
                "output": json.dumps(result, ensure_ascii=True, default=str),
            }
        )
        if isinstance(result, dict) and str(result.get("status") or "").strip().lower() != "success":
            error_text = str(result.get("message") or result.get("error") or "Skill execution failed")
            error_type = str(result.get("error_type") or _classify_tool_error(error_text))
            append_execution_event(
                {
                    "type": "tool_error",
                    "tool": "run_skill",
                    "error": error_text,
                    "error_type": error_type,
                }
            )
            result = dict(result)
            result.setdefault("error_type", error_type)
            result.setdefault("message", _tool_failure_guidance("run_skill", error_type))
        return json.dumps(result, ensure_ascii=True, default=str)
    except Exception as exc:
        error_type = _classify_tool_error(str(exc))
        err = {
            "status": "error",
            "error": str(exc),
            "error_type": error_type,
            "message": _tool_failure_guidance("run_skill", error_type),
            "skill_id": sid,
        }
        append_execution_event(
            {
                "type": "tool_error",
                "tool": "run_skill",
                "error": str(exc),
                "error_type": error_type,
            }
        )
        return json.dumps(err, ensure_ascii=True)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_react_agent(
    user_query: str | None = None,
    *,
    policy_decision: Any | None = None,
) -> LlmAgent:
    """
    Build the TitanBot ReAct agent with direct tools.

    The agent is rebuilt on each request so it gets a fresh dataset catalog
    and session context baked into its system instruction.
    """
    catalog = _build_dataset_catalog()
    context = _build_context_block()
    policy_guidance = _policy_guidance_block(policy_decision)
    system_parts = [
        "You are TitanBot, the intelligent assistant for transportation data analysis.",
        "",
        (
            "You have tools: list_datasets, get_schema, execute_query, run_conflation."
            if not AGENT_SKILLS_ENABLED
            else "You have tools: list_datasets, get_schema, execute_query, run_conflation, list_skills, run_skill."
        ),
        "Use them in a loop — call a tool, observe the result, then decide your next action.",
        "",
        "CORE RULES:",
        "1. ALWAYS call get_schema immediately before every execute_query call.",
        "   The schema reflects the current live state of the dataset — do not rely on",
        "   schema information from conversation history or a previous turn.",
        "   Only reference columns that appear in the freshly-fetched schema. Never guess.",
        "2. OBSERVE results. If row_count is 0 or data is empty, DO NOT apologise and give up.",
        "   Instead call execute_query again with looser filters (broader date range,",
        "   remove a condition, try a different grouping). Tell the user what you adjusted.",
        "3. For cross-domain questions ('crashes near workzones', 'hard braking near accidents')",
        "   use run_conflation — never try to fake this with execute_query filters.",
        "4. CONVERSATIONAL: greetings, capability questions, and general knowledge questions",
        "   don't need tool calls. Answer naturally.",
        "5. SPEAK PLAINLY: never mention JSON, SQL, execution plans, or tool names in your",
        "   final response. Talk about the data itself.",
        "6. SHOW = MAP: when the user says 'show', 'display', 'map', or 'visualize'",
        "   without further qualification, they want a map — use execute_query with a",
        "   generate_map step. Only return tabular data if they explicitly ask to 'list',",
        "   'table', 'print', 'give me the rows', etc.",
        "   If the user asks 'show me crashes' (or equivalent broad crash map requests),",
        "   prefer a pure map result and avoid unbounded raw-row outputs.",
        "   NOTE: 'plot' and 'chart' are NOT map requests — those go to the charting feature.",
        "7. CONTEXT: use [Active Road Scope], [Recent Queries], and [Prior Analysis Context]",
        "   below to interpret follow-ups like 'that', 'those roads', 'same corridor',",
        "   'same area', and 'break it down'. Prefer explicit inherited road scope over",
        "   asking the user to restate a corridor when the active scope is unambiguous.",
        "8. QUERYABLE FIELDS: Crash and workzone datasets have a configured set of queryable",
        "   columns set up by the user in the Ingestion Inspector. get_schema() returns only",
        "   the currently-enabled columns — this is intentional, not a bug.",
        "   • If a user asks about a column that's not in the schema, explain it may not be",
        "     enabled yet, and tell them to: open the Ingestion Inspector → select the dataset",
        "     → find Queryable Fields → enable the field → Save.",
        "   • When execute_query returns error_type 'queryable_policy_blocked', relay the",
        "     message to the user conversationally. No jargon, no tool names.",
        "9. PROFESSIONAL RESPONSES:",
        "   • Never expose internal IDs (dataset_id, run_id, UUIDs) in responses.",
        "     Refer to datasets by their type and date range, e.g. 'the crash dataset' or",
        "     'traffic data from Jan–Mar 2024'.",
        "   • Format column/field names as human-readable labels in responses.",
        "     Convert snake_case to Title Case (e.g. speed_mph → Speed (MPH),",
        "     hard_braking_count → Hard Braking Count, crash_severity → Crash Severity).",
        "   • When presenting tabular results, use clean labels — not raw column names.",
        "",
        "========================",
        "CURRENT SESSION DATASETS",
        "========================",
        catalog,
        "",
    ]

    if context:
        system_parts += [
            "========================",
            "SESSION CONTEXT",
            "========================",
            context,
            "",
        ]

    if AGENT_SKILLS_ENABLED:
        system_parts += [
            "========================",
            "SKILLS",
            "========================",
            "Use list_skills only when a compact reusable workflow would clearly help.",
            "Treat skills as optional compressed workflows, not mandatory routes.",
            "If inputs are unclear or the fit is weak, continue with generic tools.",
            "Do not call run_skill repeatedly in a single turn.",
            "",
        ]
        if policy_guidance:
            system_parts += [
                "========================",
                "POLICY GUIDANCE",
                "========================",
                policy_guidance,
                "",
            ]

    instruction = "\n".join(system_parts)

    tools: list[FunctionTool] = [
        FunctionTool(list_datasets),
        FunctionTool(get_schema),
        FunctionTool(execute_query),
        FunctionTool(run_conflation),
    ]
    if AGENT_SKILLS_ENABLED:
        tools.extend(
            [
                FunctionTool(list_skills),
                FunctionTool(run_skill),
            ]
        )

    return LlmAgent(
        name="TitanBotReAct",
        model=get_agent_model("react"),
        description="TitanBot transportation data analyst — ReAct loop with direct tools.",
        instruction=instruction,
        tools=tools,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_react_pipeline(user_query: str) -> str:
    """
    Run the ReAct agent for one user turn and return the final text response.
    Drop-in replacement for run_analytical_pipeline from pipeline.py.
    """
    effective_user_query = _normalize_followup_user_query(user_query)
    if effective_user_query != user_query:
        append_execution_event(
            {
                "type": "followup_scope_rewrite",
                "user_query": _compact_text(user_query, 180),
                "effective_query": _compact_text(effective_user_query, 180),
            }
        )

    if _is_ambiguous_incident_corridor_prompt(effective_user_query):
        return (
            "Do you want traffic incidents, crashes, or workzones on "
            f"{_extract_route_refs(effective_user_query)[0]}?"
        )

    logger.info(f"ReAct pipeline: '{effective_user_query}'")
    append_execution_event(
        {
            "type": "react_turn_start",
            "query": _compact_text(effective_user_query, 180),
        }
    )

    direct_skill_response = await _run_direct_skill_route(effective_user_query)
    if direct_skill_response:
        return direct_skill_response.strip()

    policy_decision = decide_policy(effective_user_query) if AGENT_SKILLS_ENABLED else None

    session_service = InMemorySessionService()
    agent = build_react_agent(
        user_query=effective_user_query,
        policy_decision=policy_decision,
    )
    runner = Runner(app_name="TitanBot", agent=agent, session_service=session_service)
    session = await session_service.create_session(app_name="TitanBot", user_id="internal")

    content = types.Content(role="user", parts=[types.Part(text=effective_user_query)])
    response_text = ""
    usage_collector = new_usage_collector("react")

    try:
        async for event in runner.run_async(
            user_id="internal",
            session_id=session.id,
            new_message=content,
        ):
            collect_usage_from_event(usage_collector, event)
            if hasattr(event, "is_final_response") and event.is_final_response():
                for part in _safe_event_parts(event):
                    text = getattr(part, "text", None)
                    if text:
                        response_text = text
                        break
    finally:
        usage_summary = finalize_usage_collector(usage_collector)
        if usage_summary:
            append_execution_event({"type": "llm_usage", **usage_summary})

    if not response_text.strip():
        logger.warning("ReAct pipeline: no final response text for query %r", effective_user_query[:200])
        return "I was unable to produce a response for that request. Please try rephrasing."

    return response_text.strip()


# ---------------------------------------------------------------------------
# Tool export (matches pipeline.py interface so agent.py needs one line change)
# ---------------------------------------------------------------------------

def get_pipeline_tool() -> FunctionTool:
    """
    Wraps the ReAct pipeline as a single tool for the root dispatcher agent.
    Matches the interface of the original get_pipeline_tool() in pipeline.py.
    """
    run_react_pipeline.__name__ = "run_data_analysis"
    return FunctionTool(run_react_pipeline)

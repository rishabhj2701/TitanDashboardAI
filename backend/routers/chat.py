from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.genai.types import Content, Part

from backend.auth.dependencies import get_current_user
from dynamic_analyst import postgis_store
from dynamic_analyst.adk_session_service import create_chat_session_service
from dynamic_analyst.agent import root_agent
from dynamic_analyst.modeling import (
    aggregate_usage_events,
    collect_usage_from_event,
    finalize_usage_collector,
    new_usage_collector,
)
from dynamic_analyst.services.datasets import list_session_datasets
from dynamic_analyst.session_state import (
    set_active_session,
    get_active_user,
    get_scoped_session_key,
    acquire_chat_lock,
    clear_chat_inflight_question,
    get_chat_inflight_question,
    set_chat_inflight_question,
    release_chat_lock,
    get_and_clear_map,
    append_execution_event,
    clear_maps_for_session,
    get_last_map,
    get_execution_events,
    clear_execution_for_session,
    get_analysis_context,
    clear_analysis_context_for_session,
)
from dynamic_analyst.pipeline_react import clear_schema_cache_for_session

router = APIRouter()
logger = logging.getLogger("adk_server")

TRACE_ID: ContextVar[str] = ContextVar("TRACE_ID", default="-")

APP_NAME = "traffic_analyst_app"
runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=create_chat_session_service(),
    artifact_service=InMemoryArtifactService(),
    memory_service=InMemoryMemoryService(),
)


def jlog(event: str, **fields):
    payload = {"event": event, "trace_id": TRACE_ID.get(), **fields}
    logger.info(json.dumps(payload, default=str))


def _summarize_parts(parts):
    out = []
    for p in parts or []:
        entry = {}
        if getattr(p, "text", None):
            entry["text"] = p.text[:400]
        fc = getattr(p, "function_call", None)
        if fc:
            entry["function_call"] = {
                "name": getattr(fc, "name", None),
                "args": getattr(fc, "args", None),
            }
        out.append(entry)
    return out


def _extract_user_question(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    text = text.split("\n\n[Dataset Context]", 1)[0].strip()
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line or text


def _compact_dataset_context(message: str, max_columns: int = 80) -> str:
    raw = (message or "").strip()
    marker = "[Dataset Context]"
    if marker not in raw:
        return raw
    before, after = raw.split(marker, 1)
    context_lines = [line.strip() for line in after.splitlines() if line.strip()]
    keep_prefixes = (
        "dataset:",
        "dataset id:",
        "format:",
        "total rows:",
        "features:",
        "columns",
        "key columns:",
        "spatial:",
        "temporal:",
        "note:",
        "if the question is ambiguous",
    )
    compact_lines: list[str] = []
    for line in context_lines:
        lowered = line.lower()
        if lowered.startswith("sample ") or lowered.startswith("full data") or lowered.startswith("[") or lowered.startswith("{"):
            break
        if lowered.startswith("columns"):
            if ":" in line:
                left, right = line.split(":", 1)
                cols = [c.strip() for c in right.split(",") if c.strip()]
                if len(cols) > max_columns:
                    clipped = ", ".join(cols[:max_columns])
                    line = f"{left}: {clipped}, ... (+{len(cols) - max_columns} more)"
                else:
                    line = f"{left}: {', '.join(cols)}"
            compact_lines.append(line)
            continue
        if any(lowered.startswith(prefix) for prefix in keep_prefixes):
            compact_lines.append(line)
    if not compact_lines:
        return before.strip()
    return f"{before.strip()}\n\n{marker}\n" + "\n".join(compact_lines)


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _row_count_for_dataset(row: dict[str, Any]) -> Optional[int]:
    direct_count = _as_int(row.get("row_count"))
    if direct_count is not None:
        return direct_count
    stats = _safe_json_dict(row.get("stats"))
    ingest = stats.get("ingest") if isinstance(stats.get("ingest"), dict) else {}
    ingest_count = _as_int(ingest.get("rows_inserted"))
    if ingest_count is not None:
        return ingest_count
    return _as_int(stats.get("rows_inserted"))


def _enabled_queryable_names(row: dict[str, Any], max_items: int = 10) -> list[str]:
    stats = _safe_json_dict(row.get("stats"))
    queryable = stats.get("queryable_fields") if isinstance(stats.get("queryable_fields"), dict) else {}
    fields = queryable.get("fields") if isinstance(queryable.get("fields"), list) else []
    names: list[str] = []
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("enabled") is False:
            continue
        candidate = str(field.get("query_name") or field.get("source_column") or "").strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        names.append(candidate)
        if len(names) >= max(1, int(max_items)):
            break
    return names


def _dataset_origin_label(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "").strip().lower()
    dataset_id = str(row.get("dataset_id") or "").strip().lower()
    entity_type = str(row.get("entity_type") or "").strip().lower()
    name = str(row.get("name") or "").strip().lower()
    if source == "cv_runs" or dataset_id.startswith("cv_run:"):
        return "backend_static_cv"
    if source == "hard_brake_table":
        return "backend_static_hard_braking"
    if entity_type == "cv" and (name == "traffic" or not row.get("created_at")):
        return "backend_static_cv"
    return "user_uploaded"


def _build_dataset_inventory_context_block(limit: int = 20) -> str:
    try:
        rows = list_session_datasets()
    except Exception:
        return ""
    if not isinstance(rows, list) or not rows:
        return ""

    lines: list[str] = [
        "[Dataset Inventory Context]",
        "Use this as the authoritative dataset inventory for this user.",
        "Origin types: backend_static_cv (built-in CV run data), user_uploaded (uploaded datasets).",
    ]
    entity_types: set[str] = set()
    for row in rows[: max(1, int(limit))]:
        if not isinstance(row, dict):
            continue
        dataset_id = str(row.get("dataset_id") or "").strip()
        if not dataset_id:
            continue
        name = str(row.get("name") or "").strip() or "unknown"
        entity_type = str(row.get("entity_type") or "").strip().lower() or "unknown"
        entity_types.add(entity_type)
        status = str(row.get("status") or "").strip() or "unknown"
        origin = _dataset_origin_label(row)
        row_count = _row_count_for_dataset(row)
        row_count_text = str(row_count) if row_count is not None else "unknown"
        parts = [
            f"dataset_id={dataset_id}",
            f"name={name}",
            f"entity_type={entity_type}",
            f"origin={origin}",
            f"status={status}",
            f"rows={row_count_text}",
        ]
        if row.get("is_active"):
            parts.append("active=true")
        run_id = str(row.get("run_id") or "").strip()
        schema_name = str(row.get("schema_name") or "").strip()
        state_code = str(row.get("state_code") or "").strip()
        ts_start = str(row.get("ts_start") or "").strip()
        ts_end = str(row.get("ts_end") or "").strip()
        if run_id:
            parts.append(f"run_id={run_id}")
        if schema_name:
            parts.append(f"schema={schema_name}")
        if state_code:
            parts.append(f"state={state_code}")
        if ts_start or ts_end:
            parts.append(f"range={ts_start or '?'}..{ts_end or '?'}")
        queryable_names = _enabled_queryable_names(row)
        if queryable_names:
            parts.append(f"queryable_fields={','.join(queryable_names)}")
        lines.append("- " + " | ".join(parts))

    if entity_types:
        lines.append("Entity types in scope: " + ", ".join(sorted(entity_types)))
    return "\n".join(lines)


def _compact_prompt_text(value: Any, max_chars: int = 5000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n... (truncated {omitted} chars)"


def _parse_event_ts(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _events_since(events: List[Dict[str, Any]], started_at: datetime) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_ts = _parse_event_ts(event.get("ts"))
        if event_ts is None or event_ts >= started_at:
            out.append(event)
    return out


def _with_usage_summary(events: List[Dict[str, Any]], usage_summary: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(usage_summary, dict):
        return list(events)
    target_role = str(usage_summary.get("role") or "").strip().lower()
    if not target_role:
        return list(events)
    out = list(events)
    for row in out:
        if not isinstance(row, dict) or str(row.get("type") or "") != "llm_usage":
            continue
        if str(row.get("role") or "").strip().lower() == target_role:
            return out
    out.append({"type": "llm_usage", **usage_summary})
    return out


def _compact_area_metrics(metrics: Any) -> Any:
    if not isinstance(metrics, dict):
        return metrics
    keys = (
        "points",
        "vehicles",
        "road_segments",
        "crashes",
        "hard_brakes",
        "avg_speed_mph",
        "speeding_pct",
        "time_start",
        "time_end",
        "approximate",
        "fast_aggregate_mode",
    )
    compact = {k: metrics.get(k) for k in keys if metrics.get(k) is not None}
    def _compact_named_counts(rows: Any, *, max_items: int = 3) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            road_name = str(row.get("road_name") or "").strip()
            if not road_name:
                continue
            out.append({"road_name": road_name, "count": int(row.get("count") or 0)})
            if len(out) >= max_items:
                break
        return out

    top_roads = _compact_named_counts(metrics.get("top_roads"))
    top_hard_brake_roads = _compact_named_counts(metrics.get("top_hard_brake_roads"))
    top_crash_roads = _compact_named_counts(metrics.get("top_crash_roads"))
    if top_roads:
        compact["top_roads"] = top_roads
    if top_hard_brake_roads:
        compact["top_hard_brake_roads"] = top_hard_brake_roads
    if top_crash_roads:
        compact["top_crash_roads"] = top_crash_roads
    return compact


def _build_recent_analysis_context_block(session_id: str, _message: str, limit: int = 1) -> str:
    rows = get_analysis_context(session_id, limit=max(1, int(limit)))
    if not rows:
        return ""
    latest = rows[-1] if isinstance(rows[-1], dict) else None
    if not latest:
        return ""
    analysis_type = str(latest.get("analysis_type") or "").strip().lower()
    compact: Dict[str, Any] = {
        "analysis_type": analysis_type or "unknown",
        "created_at": latest.get("created_at"),
        "mode": latest.get("mode"),
        "response_summary": _compact_prompt_text(latest.get("response_summary"), max_chars=420),
        "important_metrics": (
            _compact_area_metrics(latest.get("important_metrics"))
            if analysis_type == "area"
            else latest.get("important_metrics")
        ),
        "dataset_ids": latest.get("dataset_ids"),
        "geometry_meta": latest.get("geometry_meta") if analysis_type == "area" else None,
    }
    return "\n".join([
        "[Recent Analysis Context]",
        "Most recent direct-analysis context only. Use this first for follow-up questions.",
        json.dumps(compact, ensure_ascii=True),
    ])


def _latest_query_output_state_block(session_id: str) -> str:
    events = get_execution_events(session_id, limit=12)
    if not events:
        return ""

    latest_plan: Optional[Dict[str, Any]] = None
    latest_result: Optional[Dict[str, Any]] = None
    latest_error: Optional[Dict[str, Any]] = None

    for row in reversed(events):
        if not isinstance(row, dict):
            continue
        etype = str(row.get("type") or "")
        if latest_result is None and etype == "tool_result":
            latest_result = row
        if latest_error is None and etype == "tool_error":
            latest_error = row
        if latest_plan is None and etype == "tool_plan":
            latest_plan = row
        if latest_plan and (latest_result or latest_error):
            break

    if not latest_plan and not latest_result and not latest_error:
        return ""

    def _is_queryable_blocked_output(raw: Any) -> bool:
        text = _compact_prompt_text(raw, max_chars=2000).lower()
        if not text:
            return False
        if "not queryable for this dataset" in text:
            return True
        if "[queryable_policy_blocked]" in text:
            return True
        if '"status": "blocked"' in text and "queryable" in text:
            return True
        return False

    lines: list[str] = ["[Query Execution State]"]
    if latest_plan:
        lines.append(
            json.dumps(
                {
                    "tool": latest_plan.get("tool"),
                    "dataset": latest_plan.get("dataset"),
                    "dataset_id": latest_plan.get("dataset_id"),
                    "operations": latest_plan.get("operations"),
                    "query_json": _compact_prompt_text(latest_plan.get("query_json"), max_chars=1800),
                },
                ensure_ascii=True,
            )
        )
    if latest_result and not _is_queryable_blocked_output(latest_result.get("output")):
        lines.append(
            "[Latest Query Output]\n"
            + _compact_prompt_text(latest_result.get("output"), max_chars=1500)
        )
    elif latest_error:
        lines.append(
            "[Latest Query Error]\n"
            + _compact_prompt_text(latest_error.get("error"), max_chars=1200)
        )
    return "\n".join(lines)


def _build_dispatch_routing_hint(
    message: str,
    last_map: Optional[Dict[str, Any]] = None,
    query_execution_state: str = "",
    analysis_context_state: str = "",
    dataset_inventory_context: str = "",
) -> str:
    raw = message or ""
    prefix_blocks: List[str] = []
    if query_execution_state.strip():
        prefix_blocks.append(query_execution_state.strip())
    if analysis_context_state.strip():
        prefix_blocks.append(analysis_context_state.strip())
    if dataset_inventory_context.strip():
        prefix_blocks.append(dataset_inventory_context.strip())
    if isinstance(last_map, dict) and last_map:
        road_filter = last_map.get("roadAggregateFilter")
        road_scope_label = ""
        if isinstance(road_filter, dict) and road_filter:
            road_names = [str(v).strip() for v in (road_filter.get("road_names") or []) if str(v or "").strip()]
            road_refs = [str(v).strip() for v in (road_filter.get("road_refs") or []) if str(v or "").strip()]
            if road_names:
                road_scope_label = ", ".join(road_names[:3])
            elif road_refs:
                road_scope_label = ", ".join(road_refs[:3])
            else:
                road_scope_label = (
                    str(road_filter.get("road_name") or "").strip()
                    or str(road_filter.get("road_segment_id") or "").strip()
                )
        last_summary = {
            "analysis_type": last_map.get("analysis_type"),
            "label": last_map.get("label"),
            "point_count": len(last_map.get("points")) if isinstance(last_map.get("points"), list) else 0,
            "line_count": len(last_map.get("lines")) if isinstance(last_map.get("lines"), list) else 0,
            "has_road_filter": bool(road_filter),
            "road_scope_label": road_scope_label or None,
            "roadAggregateFilter": road_filter if isinstance(road_filter, dict) and road_filter else None,
        }
        prefix_blocks.append("[Last Map Context]\n" + json.dumps(last_summary, ensure_ascii=True))
    if not prefix_blocks:
        return raw
    return "\n\n".join(prefix_blocks + [raw])


def _ensure_brief_response(response_text: str) -> str:
    text = (response_text or "").strip()
    if not text:
        return text
    text = re.sub(r"^\s*brief\s*:\s*[^\n]*\n*", "", text, flags=re.IGNORECASE).strip()
    return text


class ChatRequest(BaseModel):
    message: str
    sessionId: Optional[str] = None
    history: Optional[List[Any]] = None
    fileData: Optional[Any] = None


class ClearRequest(BaseModel):
    sessionId: str
    clearData: Optional[bool] = False


@router.post("/api/chat")
async def chat_endpoint(payload: ChatRequest, current_user: dict = Depends(get_current_user)):
    session_id = payload.sessionId or str(uuid.uuid4())
    user_id = get_active_user()
    scoped_session_id = get_scoped_session_key(session_id=session_id, user_id=user_id) or session_id
    set_active_session(session_id)
    trace_id = str(uuid.uuid4())[:8]
    TRACE_ID.set(trace_id)
    t0 = time.time()
    turn_started_at = datetime.now(timezone.utc)
    map_data = None
    compact_message = _compact_dataset_context(payload.message)
    user_question = _extract_user_question(compact_message).strip().lower()
    jlog(
        "chat_request",
        session_id=scoped_session_id,
        user_id=user_id,
        message=compact_message[:800],
    )
    logger.info("Session %s: Processing Query '%s'", scoped_session_id, compact_message)
    in_flight_question = get_chat_inflight_question(scoped_session_id)
    lock_token = acquire_chat_lock(scoped_session_id)
    if lock_token is None and user_question and in_flight_question == user_question:
        jlog("chat_duplicate_ignored", session_id=scoped_session_id, question=user_question)
        return {
            "sessionId": session_id,
            "response": "I am already processing that request. Please wait for the current result.",
            "responseText": "I am already processing that request. Please wait for the current result.",
            "mapSelection": None,
        }
    if lock_token is None:
        return {
            "sessionId": session_id,
            "response": "I am processing another request for this session. Please wait a moment and retry.",
            "responseText": "I am processing another request for this session. Please wait a moment and retry.",
            "mapSelection": None,
        }
    set_chat_inflight_question(scoped_session_id, user_question)
    try:
        query_execution_state = _latest_query_output_state_block(scoped_session_id)
        analysis_context_state = _build_recent_analysis_context_block(scoped_session_id, compact_message, limit=1)
        dataset_inventory_context = _build_dataset_inventory_context_block(limit=24)
        last_map_snapshot = get_last_map(scoped_session_id)
        clear_maps_for_session(scoped_session_id)

        try:
            await runner.session_service.create_session(
                session_id=scoped_session_id,
                user_id=user_id,
                app_name=APP_NAME,
            )
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                logger.warning("Session check warning: %s", exc)

        effective_message = _build_dispatch_routing_hint(
            compact_message,
            last_map=last_map_snapshot,
            query_execution_state=query_execution_state,
            analysis_context_state=analysis_context_state,
            dataset_inventory_context=dataset_inventory_context,
        )
        user_msg_content = Content(role="user", parts=[Part(text=effective_message)])

        response_text = ""
        dispatcher_usage = new_usage_collector("dispatcher")
        dispatcher_usage_summary: Optional[Dict[str, Any]] = None
        try:
            async for event in runner.run_async(
                session_id=scoped_session_id,
                user_id=user_id,
                new_message=user_msg_content,
            ):
                collect_usage_from_event(dispatcher_usage, event)
                try:
                    if getattr(event, "content", None) and getattr(event.content, "parts", None):
                        jlog(
                            "adk_event_parts",
                            session_id=scoped_session_id,
                            parts=_summarize_parts(event.content.parts),
                        )
                except Exception as exc:
                    jlog("adk_event_parts_error", error=str(exc))
                if hasattr(event, "is_final_response") and event.is_final_response():
                    if event.content and event.content.parts:
                        texts = [p.text for p in event.content.parts if getattr(p, "text", None)]
                        if texts:
                            response_text = "\n".join(texts)
        finally:
            dispatcher_usage_summary = finalize_usage_collector(dispatcher_usage)
            if dispatcher_usage_summary:
                append_execution_event({"type": "llm_usage", **dispatcher_usage_summary}, session_id=scoped_session_id)

        map_data = get_and_clear_map(scoped_session_id)
        chart_payload: List[Dict[str, Any]] = []
        if isinstance(map_data, dict):
            candidate = map_data.get("chartPayload")
            if candidate is None:
                candidate = map_data.get("chart_payload")
            if isinstance(candidate, list):
                chart_payload = [item for item in candidate if isinstance(item, dict)]
            elif isinstance(candidate, dict):
                chart_payload = [candidate]
        if not response_text:
            response_text = "Task completed (no text output)."
        else:
            response_text = _ensure_brief_response(response_text)

        turn_events = _with_usage_summary(
            _events_since(get_execution_events(scoped_session_id, limit=80), turn_started_at),
            dispatcher_usage_summary,
        )
        agent_run_metadata = aggregate_usage_events(turn_events)

        elapsed_ms = int((time.time() - t0) * 1000)
        jlog(
            "chat_response",
            session_id=scoped_session_id,
            elapsed_ms=elapsed_ms,
            response_preview=(response_text[:400] if response_text else ""),
            has_map=bool(map_data) if map_data is not None else False,
        )

        return {
            "sessionId": session_id,
            "response": response_text,
            "responseText": response_text,
            "mapSelection": map_data,
            "chartPayload": chart_payload,
            "agentRunMetadata": agent_run_metadata,
        }

    except Exception as exc:
        logger.error("Error executing agent: %s", exc, exc_info=True)
        try:
            turn_events = _with_usage_summary(
                _events_since(get_execution_events(scoped_session_id, limit=80), turn_started_at),
                dispatcher_usage_summary if "dispatcher_usage_summary" in locals() else None,
            )
            agent_run_metadata = aggregate_usage_events(turn_events)
        except Exception:
            pass
        return {
            "sessionId": session_id,
            "response": f"Error: {str(exc)}",
            "responseText": f"Error: {str(exc)}",
            "agentRunMetadata": agent_run_metadata if "agent_run_metadata" in locals() else aggregate_usage_events([]),
        }
    finally:
        clear_chat_inflight_question(scoped_session_id)
        release_chat_lock(scoped_session_id, lock_token)


@router.post("/api/chat/clear")
async def clear_session_endpoint(payload: ClearRequest, current_user: dict = Depends(get_current_user)):
    session_id = payload.sessionId
    user_id = get_active_user()
    scoped_session_id = get_scoped_session_key(session_id=session_id, user_id=user_id) or session_id
    logger.info("Clearing session: %s", scoped_session_id)

    try:
        if hasattr(runner.session_service, "delete_session"):
            try:
                await runner.session_service.delete_session(
                    app_name=APP_NAME,
                    user_id=user_id,
                    session_id=scoped_session_id,
                )
            except TypeError:
                await runner.session_service.delete_session()

        await runner.session_service.create_session(
            session_id=scoped_session_id,
            user_id=user_id,
            app_name=APP_NAME,
        )
    except Exception as exc:
        logger.warning("Note during clear: %s", exc)

    try:
        clear_maps_for_session(scoped_session_id)
    except Exception as exc:
        logger.warning("Map clear warning: %s", exc)
    try:
        clear_execution_for_session(scoped_session_id)
    except Exception as exc:
        logger.warning("Execution-state clear warning: %s", exc)
    try:
        clear_analysis_context_for_session(scoped_session_id)
    except Exception as exc:
        logger.warning("Analysis-context clear warning: %s", exc)
    try:
        clear_schema_cache_for_session(scoped_session_id)
    except Exception as exc:
        logger.warning("Schema-cache clear warning: %s", exc)

    cleared = None
    if payload.clearData:
        try:
            cleared = postgis_store.clear_session_data(session_id)
            jlog(
                "audit_session_clear_data",
                session_id=scoped_session_id,
                user_id=user_id,
                clear_data=True,
                cleared=cleared,
            )
        except Exception as exc:
            logger.warning("PostGIS clear warning: %s", exc)

    return {
        "status": "cleared",
        "sessionId": session_id,
        "cleared": cleared,
        "dataCleared": bool(payload.clearData),
    }

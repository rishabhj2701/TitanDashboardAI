from __future__ import annotations

import ast
import copy
import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from dynamic_analyst import postgis_store
from dynamic_analyst.config import CRASH_TIMEZONE
from dynamic_analyst.services.cv import latest_cv_dataset_id as _latest_cv_dataset_id
from dynamic_analyst.services.maps import make_workzone_map_payload as _make_workzone_map_payload
from dynamic_analyst.session_state import append_analysis_context, get_active_user, set_active_session
from dynamic_analyst.storage.postgis.table_names import APP_DATASETS, APP_EVENTS, APP_USER_CV_RUN_CONFIG

from backend.rams_geometry import (
    crash_analysis_segment_braking,
    crash_analysis_segment_buckets,
    crash_analysis_segment_summary,
    nearest_rams_route_id,
    route_line_geojson,
    segment_base_counts,
    use_rams_segment_cv,
)

router = APIRouter()
logger = logging.getLogger("adk_server")
_CV_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORKZONE_ANALYSIS_STMT_TIMEOUT_MS = int(os.environ.get("WORKZONE_ANALYSIS_STMT_TIMEOUT_MS", "90000"))
_WORKZONE_ANALYSIS_MAX_MAP_POINTS = int(os.environ.get("WORKZONE_ANALYSIS_MAX_MAP_POINTS", "5000"))
_AREA_ANALYSIS_STMT_TIMEOUT_MS = int(os.environ.get("AREA_ANALYSIS_STMT_TIMEOUT_MS", "300000"))
_AREA_ANALYSIS_AUTO_AGGREGATE_AREA_KM2 = float(os.environ.get("AREA_ANALYSIS_AUTO_AGGREGATE_AREA_KM2", "180"))
_AREA_ANALYSIS_DEFAULT_MAP_POINTS = int(os.environ.get("AREA_ANALYSIS_DEFAULT_MAP_POINTS", "25000"))
_AREA_ANALYSIS_DEFAULT_HB_MAP_POINTS = int(os.environ.get("AREA_ANALYSIS_DEFAULT_HB_MAP_POINTS", "10000"))
_AREA_ANALYSIS_DEFAULT_MAX_ROADS = int(os.environ.get("AREA_ANALYSIS_DEFAULT_MAX_ROADS", "12000"))
_AREA_ANALYSIS_DEFAULT_MIN_ROAD_POINTS = int(os.environ.get("AREA_ANALYSIS_DEFAULT_MIN_ROAD_POINTS", "20"))
_AREA_ANALYSIS_SEGMENT_OVERLAP_TOLERANCE_M = float(os.environ.get("AREA_ANALYSIS_SEGMENT_OVERLAP_TOLERANCE_M", "25"))
_AREA_ANALYSIS_FAST_APPROX_ENABLED = os.environ.get("AREA_ANALYSIS_FAST_APPROX_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
_AREA_ANALYSIS_FAST_APPROX_AREA_KM2 = float(os.environ.get("AREA_ANALYSIS_FAST_APPROX_AREA_KM2", "0"))
_AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_TOLERANCE_M = float(
    os.environ.get("AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_TOLERANCE_M", "20")
)
_AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_MIN_AREA_KM2 = float(
    os.environ.get("AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_MIN_AREA_KM2", "200")
)
_AREA_ANALYSIS_MAX_MAP_POINTS_CAP = int(os.environ.get("AREA_ANALYSIS_MAX_MAP_POINTS_CAP", "100000"))
_AREA_ANALYSIS_MAX_HB_MAP_POINTS_CAP = int(os.environ.get("AREA_ANALYSIS_MAX_HB_MAP_POINTS_CAP", "50000"))
_AREA_ANALYSIS_MAX_ROADS_CAP = int(os.environ.get("AREA_ANALYSIS_MAX_ROADS_CAP", "50000"))
_AREA_ANALYSIS_AGGREGATE_CRASH_MAP_POINTS = int(os.environ.get("AREA_ANALYSIS_AGGREGATE_CRASH_MAP_POINTS", "2000"))
_AREA_ANALYSIS_AGGREGATE_HB_MAP_POINTS = int(os.environ.get("AREA_ANALYSIS_AGGREGATE_HB_MAP_POINTS", "4000"))
_AREA_ANALYSIS_FAST_AGGREGATE_HB_MAP_POINTS = int(os.environ.get("AREA_ANALYSIS_FAST_AGGREGATE_HB_MAP_POINTS", "3000"))
_AREA_ANALYSIS_CACHE_TTL_S = float(os.environ.get("AREA_ANALYSIS_CACHE_TTL_S", "240"))
_AREA_ANALYSIS_CACHE_MAX_ENTRIES = int(os.environ.get("AREA_ANALYSIS_CACHE_MAX_ENTRIES", "32"))
_AREA_ANALYSIS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id header")
    set_active_session(x_session_id)
    return x_session_id

def _require_valid_cv_schema_name(schema_name: str) -> str:
    schema = (schema_name or "").strip()
    if not schema or not _CV_SCHEMA_NAME_RE.match(schema):
        raise ValueError(f"Invalid schema name '{schema_name}'.")
    return schema


class CrashAnalyzeRequest(BaseModel):
    crash_lat: float
    crash_lon: float
    crash_ts: Optional[str] = None
    accident_date: Optional[str] = None
    accident_time: Optional[str] = None
    severity: Optional[str] = None
    road_segment_id: Optional[str] = None
    crash_id: Optional[str] = None
    dataset_id: Optional[str] = None
    cv_dataset_id: Optional[str] = None
    distance_m: float = 200.0
    window_minutes: int = 60
    enable_widening: bool = False


class WorkzoneAnalyzeRequest(BaseModel):
    workzone_id: str
    road_segment_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    dataset_id: Optional[str] = None
    cv_dataset_id: Optional[str] = None
    distance_m: float = 200.0


class AreaAnalyzeRequest(BaseModel):
    polygon: Dict[str, Any]
    cv_dataset_id: Optional[str] = None
    crash_dataset_id: Optional[str] = None
    workzone_dataset_id: Optional[str] = None
    include_unmatched: bool = False
    analysis_mode: Optional[str] = "aggregate"
    hard_brake_group_by: Optional[str] = "segment"
    max_map_points: Optional[int] = None
    max_hard_brake_points: Optional[int] = None
    max_roads: Optional[int] = None
    min_road_points: Optional[int] = None


def _clamp_int(value: Optional[int], *, default: int, min_value: int, max_value: int) -> int:
    try:
        candidate = int(value) if value is not None else int(default)
    except Exception:
        candidate = int(default)
    return max(min_value, min(max_value, candidate))


def _normalize_area_analysis_mode(value: Optional[str]) -> str:
    mode = str(value or "aggregate").strip().lower()
    if mode not in {"auto", "detail", "aggregate"}:
        raise ValueError(f"Invalid analysis_mode '{value}'. Expected auto/detail/aggregate.")
    return mode


def _normalize_hard_brake_group_by(value: Optional[str]) -> str:
    group_by = str(value or "segment").strip().lower()
    if group_by in {"segment", "road_segment", "road_segment_id", "way", "way_id"}:
        return "segment"
    if group_by in {"road", "road_name", "name"}:
        return "road_name"
    if group_by in {"ref", "route", "road_ref", "highway_ref"}:
        return "ref"
    raise ValueError(
        f"Invalid hard_brake_group_by '{value}'. Expected segment/road_name/ref."
    )


def _normalize_hourly_unique_vehicles(raw: Any) -> Optional[dict[str, float]]:
    parsed = raw
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None

    items: list[tuple[int, float]] = []
    for hour_key_raw, hour_value_raw in parsed.items():
        try:
            hour = int(str(hour_key_raw).strip())
            value = float(hour_value_raw)
        except Exception:
            continue
        if hour < 0 or hour > 23 or not math.isfinite(value):
            continue
        items.append((hour, max(0.0, value)))

    if not items:
        return None
    items.sort(key=lambda item: item[0])
    return {str(hour): value for hour, value in items}


def _avg_unique_vehicles_per_hour_from_hourly(
    hourly_unique_vehicles: Optional[dict[str, float]],
) -> Optional[float]:
    if not hourly_unique_vehicles:
        return None
    total = 0.0
    for hour in range(24):
        total += float(hourly_unique_vehicles.get(str(hour), 0.0) or 0.0)
    return total / 24.0


def _area_analysis_cache_get(key: str) -> Optional[dict[str, Any]]:
    entry = _AREA_ANALYSIS_CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    now = time.perf_counter()
    if now - ts > _AREA_ANALYSIS_CACHE_TTL_S:
        _AREA_ANALYSIS_CACHE.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _area_analysis_cache_put(key: str, payload: dict[str, Any]) -> None:
    now = time.perf_counter()
    _AREA_ANALYSIS_CACHE[key] = (now, copy.deepcopy(payload))
    expired_keys = [k for k, (ts, _) in _AREA_ANALYSIS_CACHE.items() if now - ts > _AREA_ANALYSIS_CACHE_TTL_S]
    for stale in expired_keys:
        _AREA_ANALYSIS_CACHE.pop(stale, None)
    while len(_AREA_ANALYSIS_CACHE) > _AREA_ANALYSIS_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(_AREA_ANALYSIS_CACHE))
        _AREA_ANALYSIS_CACHE.pop(oldest_key, None)


def _area_analysis_cache_key(
    *,
    user_id: str,
    mode: str,
    cv_dataset_id: Optional[str],
    crash_dataset_id: Optional[str],
    workzone_dataset_id: Optional[str],
    include_unmatched: bool,
    min_road_points: int,
    max_roads: int,
    polygon_json: str,
) -> str:
    polygon_hash = hashlib.sha1(polygon_json.encode("utf-8")).hexdigest()
    return "|".join(
        [
            user_id or "dev-user",
            mode,
            cv_dataset_id or "",
            crash_dataset_id or "",
            workzone_dataset_id or "",
            "1" if include_unmatched else "0",
            str(min_road_points),
            str(max_roads),
            polygon_hash,
        ]
    )


def _normalize_iso_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _normalize_iso_time(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[1]
    if "Z" in s:
        s = s.split("Z", 1)[0]
    if "+" in s:
        s = s.split("+", 1)[0]
    if "-" in s:
        idx = s.rfind("-")
        if idx > 2:
            s = s[:idx]
    s = s.strip()
    if len(s) >= 8:
        s = s[:8]
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            second = int(parts[2]) if len(parts) >= 3 else 0
        except Exception:
            return None
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return f"{hour:02d}:{minute:02d}:{second:02d}"
    return None


def _normalize_workzone_row_id(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return None

    # Tolerate ids emitted as "<event_id>-<line_idx>".
    m = re.match(r"^(\d+)(?:-\d+)?$", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _normalize_crash_ts_utc(value: Optional[str], crash_tz: str) -> Optional[str]:
    """
    Normalize a crash timestamp string to a UTC ISO string.
    - If input has timezone, convert to UTC.
    - If input is naive, interpret it in crash_tz and convert to UTC.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(crash_tz))
        except Exception:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _latest_event_dataset_id(_session_id: str, entity_type: str) -> Optional[str]:
    uid = (get_active_user() or "dev-user").strip() or "dev-user"
    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_id
                FROM """ + APP_DATASETS + """
                WHERE owner_user_id=%s AND entity_type=%s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (uid, entity_type),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _latest_workzone_dataset_id(session_id: str) -> Optional[str]:
    return _latest_event_dataset_id(session_id, "workzone")


def _parse_geojson_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.dumps(json.loads(text))
    except Exception:
        try:
            return json.dumps(ast.literal_eval(text))
        except Exception:
            return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except Exception:
        return 0


def _compact_response_summary(response_text: str, *, max_lines: int = 8, max_chars: int = 1200) -> str:
    lines = [line.strip() for line in str(response_text or "").splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    summary = "\n".join(lines).strip()
    if len(summary) > max_chars:
        omitted = len(summary) - max_chars
        summary = f"{summary[:max_chars]} ... (+{omitted} chars)"
    return summary


def _top_named_counts(rows: Any, *, max_items: int = 5) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        road_name = str(row.get("road_name") or row.get("roadName") or "").strip()
        if not road_name:
            continue
        out.append(
            {
                "road_name": road_name,
                "count": _safe_int(row.get("count")),
            }
        )
        if len(out) >= max_items:
            break
    return out


def _polygon_bbox_and_vertex_count(polygon: Any) -> tuple[Optional[dict[str, float]], int]:
    if not isinstance(polygon, dict):
        return None, 0
    coordinates = polygon.get("coordinates")
    if not isinstance(coordinates, list):
        return None, 0
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")
    vertex_count = 0
    for ring in coordinates:
        if not isinstance(ring, list):
            continue
        for pair in ring:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            lon = _safe_float(pair[0])
            lat = _safe_float(pair[1])
            if lon is None or lat is None:
                continue
            vertex_count += 1
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)
    if vertex_count <= 0:
        return None, 0
    return {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }, vertex_count


def _persist_analysis_context(context: Dict[str, Any], session_id: Optional[str]) -> None:
    if not isinstance(context, dict):
        return
    try:
        append_analysis_context(context, session_id=session_id)
    except Exception as exc:
        logger.warning("Failed to persist analysis context: %s", exc)


def _build_area_analysis_context(
    *,
    response_text: str,
    summary: Dict[str, Any],
    mode: str,
    polygon_json: str,
    cv_dataset_id: Optional[str],
    crash_dataset_id: Optional[str],
    workzone_dataset_id: Optional[str],
) -> Dict[str, Any]:
    polygon_obj: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(polygon_json or "")
        if isinstance(parsed, dict):
            polygon_obj = parsed
    except Exception:
        polygon_obj = None
    bbox, vertex_count = _polygon_bbox_and_vertex_count(polygon_obj)
    return {
        "analysis_type": "area",
        "response_summary": _compact_response_summary(response_text),
        "mode": str(mode or "").strip().lower() or "aggregate",
        "important_metrics": {
            "points": _safe_int(summary.get("points")),
            "vehicles": _safe_int(summary.get("vehicles")),
            "road_segments": _safe_int(summary.get("road_segments")),
            "crashes": _safe_int(summary.get("crashes")),
            "hard_brakes": _safe_int(summary.get("hard_brakes")),
            "avg_speed_mph": _safe_float(summary.get("avg_speed")),
            "speeding_pct": _safe_float(summary.get("speeding_pct")),
            "time_start": summary.get("time_start"),
            "time_end": summary.get("time_end"),
            "top_roads": _top_named_counts(summary.get("top_roads")),
            "top_hard_brake_roads": _top_named_counts(summary.get("hard_brake_by_road")),
            "hard_brake_group_by": summary.get("hard_brake_group_by"),
            "top_crash_roads": _top_named_counts(summary.get("crash_by_road")),
            "approximate": bool(summary.get("approximate")),
            "fast_aggregate_mode": bool(summary.get("fast_aggregate_mode")),
        },
        "dataset_ids": {
            "cv_dataset_id": cv_dataset_id,
            "crash_dataset_id": crash_dataset_id,
            "workzone_dataset_id": workzone_dataset_id,
        },
        "geometry_meta": {
            "polygon_hash": hashlib.sha1((polygon_json or "").encode("utf-8")).hexdigest(),
            "bbox": bbox,
            "vertex_count": vertex_count,
            "area_km2": _safe_float(summary.get("area_km2")),
        },
        "geometry": polygon_obj,
    }


def _build_crash_analysis_context(
    *,
    response_text: str,
    payload: CrashAnalyzeRequest,
    params: Dict[str, Any],
    summary: Dict[str, Any],
    braking: Dict[str, Any],
    road_segment_id: Optional[str],
    crash_road_name: Optional[str],
    workzone_lines_count: int,
    cv_dataset_id: Optional[str],
    workzone_dataset_id: Optional[str],
) -> Dict[str, Any]:
    return {
        "analysis_type": "crash",
        "response_summary": _compact_response_summary(response_text),
        "important_metrics": {
            "distance_m": _safe_float(params.get("distance_m")),
            "window_minutes": _safe_int(params.get("window_minutes")),
            "cv_points": _safe_int(summary.get("points")),
            "vehicles": _safe_int(summary.get("vehicles")),
            "avg_speed_mph": _safe_float(summary.get("avg_speed")),
            "hard_braking_events": _safe_int(braking.get("hard_braking_events")),
            "hard_braking_vehicles": _safe_int(braking.get("hard_braking_vehicles")),
            "workzones_within_500m": _safe_int(workzone_lines_count),
            "road_segment_id": road_segment_id,
            "road_name": crash_road_name,
        },
        "dataset_ids": {
            "cv_dataset_id": cv_dataset_id,
            "workzone_dataset_id": workzone_dataset_id,
        },
        "anchor": {
            "crash_id": payload.crash_id,
            "latitude": _safe_float(payload.crash_lat),
            "longitude": _safe_float(payload.crash_lon),
            "crash_ts": payload.crash_ts,
            "accident_date": payload.accident_date,
            "accident_time": payload.accident_time,
        },
    }


def _build_workzone_analysis_context(
    *,
    response_text: str,
    payload: WorkzoneAnalyzeRequest,
    summary: Dict[str, Any],
    braking: Dict[str, Any],
    crash_points_total: int,
    road_segment_id: Optional[str],
    road_name: Optional[str],
    distance_m: float,
    effective_during_start: Any,
    effective_during_end: Any,
    cv_dataset_id: Optional[str],
    crash_dataset_id: Optional[str],
    workzone_dataset_id: Optional[str],
) -> Dict[str, Any]:
    return {
        "analysis_type": "workzone",
        "response_summary": _compact_response_summary(response_text),
        "important_metrics": {
            "distance_m": _safe_float(distance_m),
            "cv_points": _safe_int(summary.get("with_points")),
            "vehicles": _safe_int(summary.get("with_vehicles")),
            "avg_speed_mph": _safe_float(summary.get("with_avg_speed")),
            "spatial_points": _safe_int(summary.get("spatial_points")),
            "conflated_points": _safe_int(summary.get("conflated_points")),
            "hard_braking_events": _safe_int(braking.get("hard_braking_events")),
            "hard_braking_vehicles": _safe_int(braking.get("hard_braking_vehicles")),
            "crashes_within_500m": _safe_int(crash_points_total),
            "road_segment_id": road_segment_id,
            "road_name": road_name,
            "during_start": str(effective_during_start) if effective_during_start is not None else None,
            "during_end": str(effective_during_end) if effective_during_end is not None else None,
        },
        "dataset_ids": {
            "cv_dataset_id": cv_dataset_id,
            "crash_dataset_id": crash_dataset_id,
            "workzone_dataset_id": workzone_dataset_id,
        },
        "anchor": {
            "workzone_id": payload.workzone_id,
        },
    }


_ROAD_ATTR_ROUTE_KEYS = (
    "ref",
    "Ref",
    "route",
    "Route",
    "highway_ref",
    "highwayRef",
)

_ROAD_ATTR_NAME_KEYS = (
    "road",
    "RoadName",
    "roadName",
    "road_name",
    "original_road",
)


def _nullif_trim_text_expr(expr: str) -> str:
    return f"NULLIF(TRIM(({expr})::text), '')"


def _normalized_route_ref_expr(ref_expr: str) -> str:
    # Normalize route references generically (e.g., I 70 -> I-70, US 63 -> US-63).
    ref_token = (
        "NULLIF("
        f"TRIM(SPLIT_PART(REGEXP_REPLACE(UPPER(COALESCE(({ref_expr})::text, '')), '\\s+', ' ', 'g'), ';', 1)), "
        "''"
        ")"
    )
    ref_compact = f"REGEXP_REPLACE({ref_token}, '[.]', '', 'g')"
    return (
        "CASE "
        f"WHEN {ref_token} IS NULL THEN NULL "
        f"WHEN {ref_compact} ~ '^(?:I|IS|INTERSTATE)[ -]*[0-9A-Z]+$' "
        f"THEN 'I-' || SUBSTRING({ref_compact} FROM '^(?:I|IS|INTERSTATE)[ -]*([0-9A-Z]+)$') "
        f"WHEN {ref_compact} ~ '^(?:US|U S)[ -]*[0-9A-Z]+$' "
        f"THEN 'US-' || SUBSTRING({ref_compact} FROM '^(?:US|U S)[ -]*([0-9A-Z]+)$') "
        f"WHEN {ref_compact} ~ '^(?:MO|STATE)[ -]*[0-9A-Z]+$' "
        f"THEN 'MO-' || SUBSTRING({ref_compact} FROM '^(?:MO|STATE)[ -]*([0-9A-Z]+)$') "
        f"WHEN {ref_compact} ~ '^(?:SR)[ -]*[0-9A-Z]+$' "
        f"THEN 'SR-' || SUBSTRING({ref_compact} FROM '^(?:SR)[ -]*([0-9A-Z]+)$') "
        f"WHEN {ref_compact} ~ '^(?:HWY|HIGHWAY)[ -]*[0-9A-Z]+$' "
        f"THEN 'HWY-' || SUBSTRING({ref_compact} FROM '^(?:HWY|HIGHWAY)[ -]*([0-9A-Z]+)$') "
        f"ELSE {ref_token} "
        "END"
    )


def _road_stats_name_kwargs(cols: set[str], alias: str = "rs") -> Dict[str, str]:
    """SQL fragments for road display names — only references columns that exist."""
    prefix = f"{alias}."
    kwargs: Dict[str, str] = {}
    for col in ("ref", "route", "highway_ref", "highwayref", "label", "road_segment_id", "way_id"):
        if col in cols:
            kwargs["ref_expr"] = f"{prefix}{col}"
            break
    if "label" in cols:
        kwargs["label_expr"] = f"{prefix}label"
    if "name" in cols:
        kwargs["name_expr"] = f"{prefix}name"
    elif "road_name" in cols:
        kwargs["name_expr"] = f"{prefix}road_name"
    if "highway" in cols:
        kwargs["highway_expr"] = f"initcap(replace({prefix}highway::text, '_', ' '))"
    return kwargs


def _preferred_road_name_expr(
    *,
    ref_expr: Optional[str] = None,
    label_expr: Optional[str] = None,
    name_expr: Optional[str] = None,
    highway_expr: Optional[str] = None,
    extra_exprs: Optional[List[str]] = None,
) -> str:
    parts: List[str] = []
    if ref_expr:
        parts.append(_normalized_route_ref_expr(ref_expr))
    if label_expr:
        parts.append(_nullif_trim_text_expr(label_expr))
    if name_expr:
        parts.append(_nullif_trim_text_expr(name_expr))
    if highway_expr:
        parts.append(_nullif_trim_text_expr(highway_expr))
    for extra in (extra_exprs or []):
        if extra:
            parts.append(extra)
    return f"COALESCE({', '.join(parts)})" if parts else "NULL::text"


def _attrs_road_name_expr(attrs_expr: str = "attrs") -> str:
    route_parts = [f"NULLIF({attrs_expr}->>'{key}','')" for key in _ROAD_ATTR_ROUTE_KEYS]
    route_expr = "COALESCE(" + ", ".join(route_parts) + ")"
    name_parts = [f"NULLIF({attrs_expr}->>'{key}','')" for key in _ROAD_ATTR_NAME_KEYS]
    name_expr = "COALESCE(" + ", ".join(name_parts) + ")"
    return _preferred_road_name_expr(ref_expr=route_expr, name_expr=name_expr)


def _roads_row_name_expr(row_expr: str = "r") -> str:
    # to_jsonb(row) lets us safely read optional columns (e.g., ref) without schema-specific SQL.
    return _preferred_road_name_expr(
        ref_expr=f"COALESCE(NULLIF(to_jsonb({row_expr})->>'ref',''), NULLIF(to_jsonb({row_expr})->>'route',''))",
        label_expr=f"to_jsonb({row_expr})->>'label'",
        name_expr=f"to_jsonb({row_expr})->>'name'",
        highway_expr=f"initcap(replace(to_jsonb({row_expr})->>'highway', '_', ' '))",
    )


def _active_cv_schema_name(cur) -> Optional[str]:
    try:
        uid = (get_active_user() or "dev-user").strip() or "dev-user"
        cur.execute("SELECT to_regclass('public.cv_runs') AS rel_runs")
        row = cur.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            if not row.get("rel_runs"):
                return None
        else:
            if not row[0]:
                return None
        row = None
        cur.execute("SELECT to_regclass(%s) AS rel_cfg", (APP_USER_CV_RUN_CONFIG,))
        user_cfg = cur.fetchone()
        has_user_cfg = (user_cfg or {}).get("rel_cfg") if isinstance(user_cfg, dict) else bool(user_cfg and user_cfg[0])
        if has_user_cfg and uid:
            cur.execute(
                f"""
                SELECT r.schema_name
                FROM {APP_USER_CV_RUN_CONFIG} c
                JOIN public.cv_runs r ON r.run_id = c.active_run_id
                WHERE c.user_id = %s
                LIMIT 1
                """,
                (uid,),
            )
            row = cur.fetchone()
        if not row:
            cur.execute("SELECT to_regclass('public.cv_run_config') AS rel_cfg")
            global_cfg = cur.fetchone()
            has_global_cfg = (global_cfg or {}).get("rel_cfg") if isinstance(global_cfg, dict) else bool(global_cfg and global_cfg[0])
            if has_global_cfg:
                cur.execute(
                    """
                    SELECT r.schema_name
                    FROM public.cv_run_config c
                    JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.id = 1
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        if not row:
            return None
        schema_name = row.get("schema_name") if isinstance(row, dict) else row[0]
        if not schema_name:
            return None
        return _require_valid_cv_schema_name(str(schema_name))
    except Exception:
        return None


def _cv_relation_candidates(
    cur,
    relation: str,
    include_unqualified: bool = True,
    include_public: bool = True,
) -> List[str]:
    candidates: List[str] = []
    schema_name = _active_cv_schema_name(cur)
    if schema_name:
        candidates.append(f"{schema_name}.{relation}")
    if include_unqualified:
        candidates.append(relation)
    if include_public:
        candidates.append(f"public.{relation}")
    deduped: List[str] = []
    seen: set[str] = set()
    for name in candidates:
        if name and name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _relation_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (name,))
    row = cur.fetchone()
    if not row:
        return False
    if isinstance(row, dict):
        return bool(row.get("to_regclass"))
    return bool(row[0])


def _first_existing_relation(cur, candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if _relation_exists(cur, name):
            return name
    return None


def _table_cols(cur, name: str) -> set[str]:
    cur.execute(
        """
        SELECT a.attname
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass(%s)
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        (name,),
    )
    return {str(r["attname"]).lower() for r in cur.fetchall()}


def _column_type(cur, name: str, column: str) -> Optional[str]:
    cur.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod) AS typ
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass(%s)
          AND a.attname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        LIMIT 1
        """,
        (name, column),
    )
    row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row.get("typ")
    return row[0]


def _norm_sql_type(type_name: Optional[str]) -> Optional[str]:
    if not type_name:
        return None
    return re.sub(r"\s+", " ", str(type_name)).strip().lower()


def _resolve_cv_analysis_context(cur) -> Dict[str, Any]:
    cv_table = _first_existing_relation(cur, _cv_relation_candidates(cur, "cv_points"))
    if not cv_table:
        raise ValueError("cv_points table not found.")

    cv_cols = _table_cols(cur, cv_table)
    has_attrs = "attrs" in cv_cols
    has_cv_dataset_col = "dataset_id" in cv_cols

    if "geom_m" in cv_cols:
        geom_m_expr = "p.geom_m"
    elif "geom_3857" in cv_cols:
        geom_m_expr = "ST_Transform(p.geom_3857, 26915)"
    elif "geom" in cv_cols:
        geom_m_expr = "ST_Transform(p.geom, 26915)"
    elif "geom_4326" in cv_cols:
        geom_m_expr = "ST_Transform(p.geom_4326, 26915)"
    else:
        raise ValueError("cv_points has no usable geometry column (geom_m/geom_3857/geom/geom_4326).")

    lat_expr = "p.lat" if "lat" in cv_cols else (
        "p.latitude" if "latitude" in cv_cols else f"ST_Y(ST_Transform({geom_m_expr}, 4326))"
    )
    lon_expr = "p.lon" if "lon" in cv_cols else (
        "p.longitude" if "longitude" in cv_cols else f"ST_X(ST_Transform({geom_m_expr}, 4326))"
    )
    speed_expr = "p.speed::float8" if "speed" in cv_cols else (
        "COALESCE("
        "NULLIF(p.attrs->>'speed','')::float8, "
        "NULLIF(p.attrs->>'SpeedMPH','')::float8, "
        "NULLIF(p.attrs->>'speed_mph','')::float8, "
        "NULLIF(p.attrs->>'speedMPH','')::float8"
        ")" if has_attrs else "NULL::float8"
    )
    acc_x_expr = "p.acc_x::float8" if "acc_x" in cv_cols else (
        "COALESCE("
        "CASE WHEN NULLIF(p.attrs->>'acc_x','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_x')::float8 END, "
        "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END, "
        "CASE WHEN NULLIF(p.attrs->>'accX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'accX')::float8 END"
        ")" if has_attrs else "NULL::float8"
    )
    acc_y_expr = "p.acc_y::float8" if "acc_y" in cv_cols else (
        "COALESCE("
        "CASE WHEN NULLIF(p.attrs->>'acc_y','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_y')::float8 END, "
        "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END, "
        "CASE WHEN NULLIF(p.attrs->>'accY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'accY')::float8 END"
        ")" if has_attrs else "NULL::float8"
    )

    vehicle_id_expr_parts: list[str] = []
    for col in ("vehicle_id", "vehicleid", "device_id", "trip_id"):
        if col in cv_cols:
            vehicle_id_expr_parts.append(f"NULLIF(TRIM(p.{col}::text),'')")
    if has_attrs:
        vehicle_id_expr_parts.extend(
            [
                "NULLIF(TRIM(p.attrs->>'VehicleID'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicle_id'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicleId'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicleid'),'')",
                "NULLIF(TRIM(p.attrs->>'TripID'),'')",
                "NULLIF(TRIM(p.attrs->>'trip_id'),'')",
                "NULLIF(TRIM(p.attrs->>'tripId'),'')",
                "NULLIF(TRIM(p.attrs->>'DeviceID'),'')",
                "NULLIF(TRIM(p.attrs->>'device_id'),'')",
                "NULLIF(TRIM(p.attrs->>'deviceId'),'')",
            ]
        )
    vehicle_id_expr = (
        f"COALESCE({', '.join(vehicle_id_expr_parts)})"
        if vehicle_id_expr_parts
        else "NULL::text"
    )

    cv_match_table = _first_existing_relation(cur, _cv_relation_candidates(cur, "cv_point_match"))
    cv_match_cols = _table_cols(cur, cv_match_table) if cv_match_table else set()
    has_cv_match = bool(
        cv_match_table
        and {"point_id", "way_id"}.issubset(cv_match_cols)
        and "id" in cv_cols
    )

    road_stats_table = _first_existing_relation(
        cur,
        _cv_relation_candidates(cur, "cv_road_stats_mv")
        + _cv_relation_candidates(cur, "cv_road_agg"),
    )
    road_stats_cols = _table_cols(cur, road_stats_table) if road_stats_table else set()
    has_road_stats = bool(road_stats_table and "way_id" in road_stats_cols)

    cv_id_type = _column_type(cur, cv_table, "id") if "id" in cv_cols else None
    cv_way_id_type = _column_type(cur, cv_table, "way_id") if "way_id" in cv_cols else None
    cv_match_point_id_type = _column_type(cur, cv_match_table, "point_id") if has_cv_match else None
    cv_match_way_id_type = _column_type(cur, cv_match_table, "way_id") if has_cv_match else None
    road_stats_way_id_type = _column_type(cur, road_stats_table, "way_id") if has_road_stats else None

    cv_match_join_condition = "m.point_id::text = p.id::text"
    if has_cv_match and _norm_sql_type(cv_id_type) == _norm_sql_type(cv_match_point_id_type):
        cv_match_join_condition = "m.point_id = p.id"
    cv_match_join_sql = f"LEFT JOIN {cv_match_table} m ON {cv_match_join_condition}" if has_cv_match else ""

    road_join_key_expr: Optional[str] = None
    road_join_key_type: Optional[str] = None
    if "way_id" in cv_cols and has_cv_match:
        if _norm_sql_type(cv_way_id_type) == _norm_sql_type(cv_match_way_id_type):
            road_join_key_expr = "COALESCE(p.way_id, m.way_id)"
            road_join_key_type = cv_way_id_type
        else:
            road_join_key_expr = "p.way_id"
            road_join_key_type = cv_way_id_type
    elif "way_id" in cv_cols:
        road_join_key_expr = "p.way_id"
        road_join_key_type = cv_way_id_type
    elif has_cv_match:
        road_join_key_expr = "m.way_id"
        road_join_key_type = cv_match_way_id_type

    road_stats_join_sql = ""
    if has_road_stats and road_join_key_expr:
        road_stats_join_sql = (
            f"LEFT JOIN {road_stats_table} rs "
            f"ON NULLIF(TRIM((rs.way_id)::text), '') = NULLIF(TRIM(({road_join_key_expr})::text), '')"
        )

    road_segment_terms: list[str] = []
    if "road_segment_id" in cv_cols:
        road_segment_terms.append("p.road_segment_id::text")
    if "way_id" in cv_cols:
        road_segment_terms.append("p.way_id::text")
    if has_cv_match:
        road_segment_terms.append("m.way_id::text")
    road_segment_expr = f"COALESCE({', '.join(road_segment_terms)})" if road_segment_terms else "NULL::text"

    road_name_fallback_parts: list[str] = []
    if has_attrs:
        road_name_fallback_parts.append(_attrs_road_name_expr("p.attrs"))
    if "road_name" in cv_cols:
        road_name_fallback_parts.append("NULLIF(p.road_name::text,'')")
    if "name" in cv_cols:
        road_name_fallback_parts.append("NULLIF(p.name::text,'')")
    road_stats_name_kwargs = _road_stats_name_kwargs(road_stats_cols) if has_road_stats else {}
    road_name_expr = _preferred_road_name_expr(
        **road_stats_name_kwargs,
        extra_exprs=road_name_fallback_parts,
    )

    speed_limit_parts: list[str] = []
    if has_road_stats and "speed_limit_mph" in road_stats_cols:
        speed_limit_parts.append("rs.speed_limit_mph::float8")
    if "speed_limit_mph" in cv_cols:
        speed_limit_parts.append(
            "CASE WHEN NULLIF(p.speed_limit_mph::text,'') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN p.speed_limit_mph::float8 END"
        )
    if has_attrs:
        speed_limit_parts.extend(
            [
                "NULLIF(p.attrs->>'speed_limit_mph','')::float8",
                "NULLIF(p.attrs->>'speed_limit','')::float8",
                "NULLIF(p.attrs->>'speedlimit_mph','')::float8",
                "NULLIF(p.attrs->>'SpeedLimitMPH','')::float8",
                "NULLIF(p.attrs->>'speedLimit','')::float8",
                "NULLIF(p.attrs->>'SpeedLimit','')::float8",
            ]
        )
    speed_limit_expr = f"COALESCE({', '.join(speed_limit_parts)})" if speed_limit_parts else "NULL::float8"

    road_stats_geom_m_expr: Optional[str] = None
    if has_road_stats:
        if "geom_m" in road_stats_cols:
            road_stats_geom_m_expr = "rs.geom_m"
        elif "geom_3857" in road_stats_cols:
            road_stats_geom_m_expr = "ST_Transform(rs.geom_3857, 26915)"
        elif "geom_4326" in road_stats_cols:
            road_stats_geom_m_expr = "ST_Transform(rs.geom_4326, 26915)"

    return {
        "cv_table": cv_table,
        "cv_cols": cv_cols,
        "has_attrs": has_attrs,
        "has_cv_dataset_col": has_cv_dataset_col,
        "geom_m_expr": geom_m_expr,
        "lat_expr": lat_expr,
        "lon_expr": lon_expr,
        "speed_expr": speed_expr,
        "speed_limit_expr": speed_limit_expr,
        "vehicle_id_expr": vehicle_id_expr,
        "acc_x_expr": acc_x_expr,
        "acc_y_expr": acc_y_expr,
        "road_segment_expr": road_segment_expr,
        "road_name_expr": road_name_expr,
        "cv_match_table": cv_match_table,
        "road_stats_table": road_stats_table,
        "has_road_stats": has_road_stats,
        "road_stats_name_kwargs": road_stats_name_kwargs,
        "road_stats_geom_m_expr": road_stats_geom_m_expr,
        "from_sql": f"FROM {cv_table} p {cv_match_join_sql} {road_stats_join_sql}",
    }


def _resolve_hard_brake_context(cur) -> Dict[str, Any]:
    hb_table = _first_existing_relation(
        cur,
        _cv_relation_candidates(cur, "cv_hard_brake_events_mv")
        + _cv_relation_candidates(cur, "cv_hard_brake"),
    )
    if not hb_table:
        return {"hb_table": None}

    hb_cols = _table_cols(cur, hb_table)
    hb_has_attrs = "attrs" in hb_cols
    hb_has_dataset_col = "dataset_id" in hb_cols

    if "geom_m" in hb_cols:
        hb_geom_m_expr = "p.geom_m"
    elif "geom_3857" in hb_cols:
        hb_geom_m_expr = "ST_Transform(p.geom_3857, 26915)"
    elif "geom_4326" in hb_cols:
        hb_geom_m_expr = "ST_Transform(p.geom_4326, 26915)"
    elif {"lon", "lat"}.issubset(hb_cols):
        hb_geom_m_expr = "ST_Transform(ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326), 26915)"
    elif {"longitude", "latitude"}.issubset(hb_cols):
        hb_geom_m_expr = "ST_Transform(ST_SetSRID(ST_MakePoint(p.longitude, p.latitude), 4326), 26915)"
    else:
        hb_geom_m_expr = None

    hb_lat_expr = (
        "p.lat" if "lat" in hb_cols else (
            "p.latitude" if "latitude" in hb_cols else (
                f"ST_Y(ST_Transform({hb_geom_m_expr}, 4326))" if hb_geom_m_expr else "NULL::float8"
            )
        )
    )
    hb_lon_expr = (
        "p.lon" if "lon" in hb_cols else (
            "p.longitude" if "longitude" in hb_cols else (
                f"ST_X(ST_Transform({hb_geom_m_expr}, 4326))" if hb_geom_m_expr else "NULL::float8"
            )
        )
    )

    hb_vehicle_id_parts: list[str] = []
    for col in ("vehicle_id", "vehicleid", "device_id", "trip_id"):
        if col in hb_cols:
            hb_vehicle_id_parts.append(f"NULLIF(TRIM(p.{col}::text),'')")
    if hb_has_attrs:
        hb_vehicle_id_parts.extend(
            [
                "NULLIF(TRIM(p.attrs->>'VehicleID'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicle_id'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicleId'),'')",
                "NULLIF(TRIM(p.attrs->>'vehicleid'),'')",
                "NULLIF(TRIM(p.attrs->>'TripID'),'')",
                "NULLIF(TRIM(p.attrs->>'trip_id'),'')",
                "NULLIF(TRIM(p.attrs->>'tripId'),'')",
                "NULLIF(TRIM(p.attrs->>'DeviceID'),'')",
                "NULLIF(TRIM(p.attrs->>'device_id'),'')",
                "NULLIF(TRIM(p.attrs->>'deviceId'),'')",
            ]
        )
    hb_vehicle_id_expr = (
        f"COALESCE({', '.join(hb_vehicle_id_parts)})"
        if hb_vehicle_id_parts
        else "NULL::text"
    )

    hb_acc_x_expr = (
        "p.acc_x::float8" if "acc_x" in hb_cols else (
            "p.accx::float8" if "accx" in hb_cols else (
                "COALESCE("
                "CASE WHEN NULLIF(p.attrs->>'acc_x','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_x')::float8 END, "
                "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END, "
                "CASE WHEN NULLIF(p.attrs->>'accX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'accX')::float8 END"
                ")" if hb_has_attrs else "NULL::float8"
            )
        )
    )
    hb_acc_y_expr = (
        "p.acc_y::float8" if "acc_y" in hb_cols else (
            "p.accy::float8" if "accy" in hb_cols else (
                "COALESCE("
                "CASE WHEN NULLIF(p.attrs->>'acc_y','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_y')::float8 END, "
                "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END, "
                "CASE WHEN NULLIF(p.attrs->>'accY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'accY')::float8 END"
                ")" if hb_has_attrs else "NULL::float8"
            )
        )
    )

    hb_speed_expr = (
        "p.speed::float8" if "speed" in hb_cols else (
            "COALESCE("
            "CASE WHEN NULLIF(p.attrs->>'speed','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed')::float8 END, "
            "CASE WHEN NULLIF(p.attrs->>'SpeedMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'SpeedMPH')::float8 END, "
            "CASE WHEN NULLIF(p.attrs->>'speed_mph','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed_mph')::float8 END, "
            "CASE WHEN NULLIF(p.attrs->>'speedMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedMPH')::float8 END"
            ")" if hb_has_attrs else "NULL::float8"
        )
    )

    hb_speed_limit_expr = (
        "p.speed_limit::float8" if "speed_limit" in hb_cols else (
            "p.speed_limit_mph::float8" if "speed_limit_mph" in hb_cols else (
                "p.speedlimit::float8" if "speedlimit" in hb_cols else (
                    "COALESCE("
                    "CASE WHEN NULLIF(p.attrs->>'speed_limit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed_limit')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'speedLimit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedLimit')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'SpeedLimitMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'SpeedLimitMPH')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'speedlimit_mph','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedlimit_mph')::float8 END"
                    ")" if hb_has_attrs else "NULL::float8"
                )
            )
        )
    )

    hb_speed_over_limit_expr = (
        "p.speed_over_limit::float8" if "speed_over_limit" in hb_cols else (
            f"(({hb_speed_expr}) - ({hb_speed_limit_expr}))"
        )
    )

    hb_road_segment_terms: list[str] = []
    if "road_segment_id" in hb_cols:
        hb_road_segment_terms.append("p.road_segment_id::text")
    if "way_id" in hb_cols:
        hb_road_segment_terms.append("p.way_id::text")
    hb_road_segment_expr = (
        f"COALESCE({', '.join(hb_road_segment_terms)})"
        if hb_road_segment_terms
        else "NULL::text"
    )

    hb_ref_terms: list[str] = []
    for col in ("ref", "route", "highway_ref", "highwayRef"):
        if col in hb_cols:
            hb_ref_terms.append(f"NULLIF(p.{col}::text,'')")
    hb_ref_expr = f"COALESCE({', '.join(hb_ref_terms)})" if hb_ref_terms else None

    hb_name_terms: list[str] = []
    for col in ("road", "road_name", "name"):
        if col in hb_cols:
            hb_name_terms.append(f"NULLIF(p.{col}::text,'')")
    hb_name_expr = f"COALESCE({', '.join(hb_name_terms)})" if hb_name_terms else None
    hb_label_expr = "p.label::text" if "label" in hb_cols else None
    hb_highway_expr = "initcap(replace(p.highway::text, '_', ' '))" if "highway" in hb_cols else None
    hb_extra_name_parts = [_attrs_road_name_expr("p.attrs")] if hb_has_attrs else []
    hb_road_name_expr = _preferred_road_name_expr(
        ref_expr=hb_ref_expr,
        label_expr=hb_label_expr,
        name_expr=hb_name_expr,
        highway_expr=hb_highway_expr,
        extra_exprs=hb_extra_name_parts,
    )

    return {
        "hb_table": hb_table,
        "hb_cols": hb_cols,
        "has_dataset_col": hb_has_dataset_col,
        "geom_m_expr": hb_geom_m_expr,
        "lat_expr": hb_lat_expr,
        "lon_expr": hb_lon_expr,
        "speed_expr": hb_speed_expr,
        "speed_limit_expr": hb_speed_limit_expr,
        "speed_over_limit_expr": hb_speed_over_limit_expr,
        "vehicle_id_expr": hb_vehicle_id_expr,
        "acc_x_expr": hb_acc_x_expr,
        "acc_y_expr": hb_acc_y_expr,
        "road_segment_expr": hb_road_segment_expr,
        "road_name_expr": hb_road_name_expr,
    }

# Crash-centered analysis: CV points within distance + time window
@router.post("/api/crash/analyze")
def analyze_crash(
    payload: CrashAnalyzeRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    try:
        crash_ts = payload.crash_ts
        accident_date = payload.accident_date
        accident_time = payload.accident_time
        road_segment_id = payload.road_segment_id
        crash_tz = CRASH_TIMEZONE

        if isinstance(crash_ts, str) and not crash_ts.strip():
            crash_ts = None
        if isinstance(accident_date, str) and not accident_date.strip():
            accident_date = None
        if isinstance(accident_time, str) and not accident_time.strip():
            accident_time = None
        if isinstance(road_segment_id, str) and road_segment_id.strip().lower() in {"", "0", "null", "none"}:
            road_segment_id = None

        crash_ts = _normalize_crash_ts_utc(crash_ts, crash_tz)
        accident_date = _normalize_iso_date(accident_date)
        accident_time = _normalize_iso_time(accident_time)

        cv_dataset_id = payload.cv_dataset_id or _latest_cv_dataset_id()

        road_match_m = min(float(payload.distance_m), 75.0)
        crash_road_name = None

        # Build SQL once for metrics + buckets
        params = {
            "cv_dataset_id": cv_dataset_id,
            "crash_lat": payload.crash_lat,
            "crash_lon": payload.crash_lon,
            "distance_m": payload.distance_m,
            "window_minutes": payload.window_minutes,
            "road_segment_id": road_segment_id,
            "crash_ts": crash_ts,
            "accident_date": accident_date,
            "accident_time": accident_time,
            "crash_tz": crash_tz,
            "road_match_m": road_match_m,
        }

        workzone_lines: list[dict] = []
        wz_dataset_id: Optional[str] = None
        rams_segment_mode = False
        rams_route_line: Optional[dict] = None
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cv_ctx = _resolve_cv_analysis_context(cur)
            hb_ctx = _resolve_hard_brake_context(cur)
            cv_dataset_clause = "AND p.dataset_id = %(cv_dataset_id)s" if cv_ctx["has_cv_dataset_col"] and cv_dataset_id else ""
            if payload.cv_dataset_id and not cv_ctx["has_cv_dataset_col"]:
                logger.info("Crash analysis: ignoring cv_dataset_id filter because cv_points has no dataset_id column")

            rams_segment_mode = use_rams_segment_cv(cur, cv_ctx["cv_table"])
            summary: Dict[str, Any] = {}
            braking: Dict[str, Any] = {"hard_braking_events": 0, "hard_braking_vehicles": 0}
            buckets: list = []
            cv_points: list = []
            use_same_road = False
            widened_search_note: Optional[str] = None

            if not road_segment_id:
                nearest_id, nearest_name = nearest_rams_route_id(
                    cur, payload.crash_lat, payload.crash_lon, road_match_m
                )
                if nearest_id:
                    road_segment_id = nearest_id
                    crash_road_name = nearest_name
                    params["road_segment_id"] = road_segment_id

            if (
                not road_segment_id
                and cv_ctx["has_road_stats"]
                and cv_ctx["road_stats_geom_m_expr"]
            ):
                cur.execute(
                    f"""
                    WITH crash AS (
                      SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m
                    )
                    SELECT r.road_segment_id, r.road_name
                    FROM crash c
                    JOIN LATERAL (
                      SELECT
                        rs.way_id::text AS road_segment_id,
                        {_preferred_road_name_expr(**(cv_ctx.get("road_stats_name_kwargs") or {}))} AS road_name
                      FROM {cv_ctx["road_stats_table"]} rs
                      WHERE {cv_ctx["road_stats_geom_m_expr"]} IS NOT NULL
                        AND ST_DWithin({cv_ctx["road_stats_geom_m_expr"]}, c.crash_geom_m, %(road_match_m)s)
                      ORDER BY {cv_ctx["road_stats_geom_m_expr"]} <-> c.crash_geom_m
                      LIMIT 1
                    ) r ON TRUE
                    """,
                    params,
                )
                row = cur.fetchone()
                if row:
                    road_segment_id = row.get("road_segment_id")
                    crash_road_name = row.get("road_name")
                    params["road_segment_id"] = road_segment_id

            if rams_segment_mode:
                seg_road_clause = "AND s.route_id = %(road_segment_id)s" if road_segment_id else ""
                base_counts = segment_base_counts(cur, params, seg_road_clause)
                if int(base_counts.get("base_points") or 0) == 0 and bool(payload.enable_widening):
                    for cand_window in (
                        max(int(payload.window_minutes), 180),
                        max(int(payload.window_minutes), 360),
                        max(int(payload.window_minutes), 720),
                    ):
                        if int(params.get("window_minutes", 0)) >= cand_window:
                            continue
                        probe_params = {**params, "window_minutes": cand_window}
                        probe_counts = segment_base_counts(cur, probe_params, seg_road_clause)
                        if int(probe_counts.get("base_points") or 0) > 0:
                            params["window_minutes"] = cand_window
                            base_counts = probe_counts
                            widened_search_note = (
                                f"No CV segment bins at ±{int(payload.window_minutes)} min; "
                                f"expanded to ±{cand_window} min."
                            )
                            break
                use_same_road = bool(road_segment_id) and (base_counts.get("same_road_points") or 0) > 0
                if road_segment_id:
                    seg_road_clause = "AND s.route_id = %(road_segment_id)s"
                summary = crash_analysis_segment_summary(cur, params, seg_road_clause)
                braking = crash_analysis_segment_braking(cur, params, seg_road_clause)
                buckets = crash_analysis_segment_buckets(cur, params, seg_road_clause)
                rams_route_line = route_line_geojson(cur, road_segment_id) if road_segment_id else None
            else:
                def _query_base_counts(p: Dict[str, Any]) -> Dict[str, Any]:
                    cur.execute(
                        f"""
                        WITH crash AS (
                          SELECT
                            %(road_segment_id)s::text AS road_segment_id,
                            ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                            CASE
                              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                              ELSE NULL
                            END AS crash_ts
                        ),
                        base AS (
                          SELECT {cv_ctx["road_segment_expr"]} AS road_segment_id
                          {cv_ctx["from_sql"]}
                          CROSS JOIN crash c
                          WHERE {cv_ctx["geom_m_expr"]} IS NOT NULL
                            {cv_dataset_clause}
                            AND p.ts IS NOT NULL
                            AND c.crash_ts IS NOT NULL
                            AND ST_DWithin({cv_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                            AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                        AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                        )
                        SELECT
                          COUNT(*) AS base_points,
                          COUNT(*) FILTER (WHERE road_segment_id = (SELECT road_segment_id FROM crash)) AS same_road_points
                        FROM base
                        """,
                        p,
                    )
                    return cur.fetchone() or {}

                # Determine whether same-road filtering yields any points
                base_counts = _query_base_counts(params)
                base_points = int(base_counts.get("base_points") or 0)

                # Optional widening: only enabled when explicitly requested.
                if base_points == 0 and bool(payload.enable_widening):
                    candidate_pairs = [
                        (max(float(payload.distance_m), 300.0), max(int(payload.window_minutes), 180)),
                        (max(float(payload.distance_m), 500.0), max(int(payload.window_minutes), 360)),
                        (max(float(payload.distance_m), 1000.0), max(int(payload.window_minutes), 720)),
                    ]
                    for cand_distance, cand_window in candidate_pairs:
                        if (
                            float(params.get("distance_m", 0)) >= cand_distance
                            and int(params.get("window_minutes", 0)) >= cand_window
                        ):
                            continue
                        probe_params = {**params, "distance_m": cand_distance, "window_minutes": cand_window}
                        probe_counts = _query_base_counts(probe_params)
                        probe_points = int(probe_counts.get("base_points") or 0)
                        if probe_points > 0:
                            params["distance_m"] = cand_distance
                            params["window_minutes"] = cand_window
                            base_counts = probe_counts
                            base_points = probe_points
                            widened_search_note = (
                                f"No CV points found at {float(payload.distance_m):.0f}m ±{int(payload.window_minutes)} min; "
                                f"expanded to {cand_distance:.0f}m ±{cand_window} min."
                            )
                            break

                use_same_road = (base_counts.get("same_road_points") or 0) > 0
                road_clause = (
                    f"AND {cv_ctx['road_segment_expr']} = c.road_segment_id"
                    if use_same_road and road_segment_id
                    else ""
                )
                logger.info(
                    "Crash analysis CV filter debug: cv_dataset_id=%s road_segment_id=%s crash_ts=%s accident_date=%s accident_time=%s "
                    "distance_m=%s window_minutes=%s base_points=%s same_road_points=%s use_same_road=%s allow_widen=%s widened=%s",
                    cv_dataset_id,
                    road_segment_id,
                    crash_ts,
                    accident_date,
                    accident_time,
                    params.get("distance_m"),
                    params.get("window_minutes"),
                    base_counts.get("base_points"),
                    base_counts.get("same_road_points"),
                    use_same_road,
                    bool(payload.enable_widening),
                    widened_search_note,
                )

                # Summary metrics
                cur.execute(
                    f"""
                    WITH crash AS (
                      SELECT
                        %(road_segment_id)s::text AS road_segment_id,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                        CASE
                          WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                          WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                            THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                          ELSE NULL
                        END AS crash_ts
                    ),
                    filtered AS (
                      SELECT
                        {cv_ctx["speed_expr"]} AS speed,
                        {cv_ctx["speed_limit_expr"]} AS speed_limit,
                        {cv_ctx["vehicle_id_expr"]} AS vehicle_id
                      {cv_ctx["from_sql"]}
                      CROSS JOIN crash c
                      WHERE {cv_ctx["geom_m_expr"]} IS NOT NULL
                        {cv_dataset_clause}
                        AND p.ts IS NOT NULL
                        AND c.crash_ts IS NOT NULL
                        AND ST_DWithin({cv_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                        AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                        {road_clause}
                    ),
                    limits AS (
                      SELECT AVG(speed_limit) AS avg_speed_limit
                      FROM filtered
                      WHERE speed_limit IS NOT NULL
                    )
                    SELECT
                      COUNT(*) AS points,
                      COUNT(DISTINCT vehicle_id) AS vehicles,
                      AVG(speed) AS avg_speed,
                      (SELECT avg_speed_limit FROM limits) AS avg_speed_limit,
                      AVG(speed - COALESCE(speed_limit, (SELECT avg_speed_limit FROM limits))) AS avg_speed_over_limit
                    FROM filtered
                    """,
                    params,
                )
                summary = cur.fetchone() or {}

                # Hard braking events from the curated hard-brake table
                braking = {"hard_braking_events": 0, "hard_braking_vehicles": 0}
                debug_braking: Dict[str, Any] = {}
                if hb_ctx.get("hb_table") and hb_ctx.get("geom_m_expr"):
                    hb_dataset_clause = "AND p.dataset_id = %(cv_dataset_id)s" if hb_ctx["has_dataset_col"] and cv_dataset_id else ""
                    hb_road_clause = (
                        f"AND {hb_ctx['road_segment_expr']} = c.road_segment_id"
                        if use_same_road and road_segment_id and hb_ctx["road_segment_expr"] != "NULL::text"
                        else ""
                    )
                    cur.execute(
                        f"""
                        WITH crash AS (
                          SELECT
                            %(road_segment_id)s::text AS road_segment_id,
                            ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                            CASE
                              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                              ELSE NULL
                            END AS crash_ts
                        ),
                        filtered AS (
                          SELECT
                            p.ts,
                            {hb_ctx["vehicle_id_expr"]} AS vehicle_id
                          FROM {hb_ctx["hb_table"]} p, crash c
                          WHERE {hb_ctx["geom_m_expr"]} IS NOT NULL
                            {hb_dataset_clause}
                            AND p.ts IS NOT NULL
                            AND c.crash_ts IS NOT NULL
                            AND ST_DWithin({hb_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                            AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                        AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                            {hb_road_clause}
                        )
                        SELECT
                          COUNT(*) AS hard_braking_events,
                          COUNT(DISTINCT vehicle_id) AS hard_braking_vehicles
                        FROM filtered
                        """,
                        params,
                    )
                    braking = cur.fetchone() or braking

                    # Hard braking debug stats (logs only)
                    cur.execute(
                        f"""
                        WITH crash AS (
                          SELECT
                            %(road_segment_id)s::text AS road_segment_id,
                            ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                            CASE
                              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                              ELSE NULL
                            END AS crash_ts
                        ),
                        filtered AS (
                          SELECT
                            p.ts,
                            {hb_ctx["acc_x_expr"]} AS acc_x,
                            {hb_ctx["vehicle_id_expr"]} AS vehicle_id
                          FROM {hb_ctx["hb_table"]} p, crash c
                          WHERE {hb_ctx["geom_m_expr"]} IS NOT NULL
                            {hb_dataset_clause}
                            AND p.ts IS NOT NULL
                            AND c.crash_ts IS NOT NULL
                            AND ST_DWithin({hb_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                            AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                        AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                            {hb_road_clause}
                        )
                        SELECT
                          (SELECT crash_ts FROM crash) AS crash_ts,
                          (SELECT COUNT(*) FROM filtered) AS points,
                          (SELECT COUNT(*) FROM filtered WHERE vehicle_id IS NOT NULL) AS points_with_vehicle_id,
                          (SELECT COUNT(DISTINCT vehicle_id) FROM filtered WHERE vehicle_id IS NOT NULL) AS vehicles_with_id,
                          (SELECT COUNT(*) FROM filtered WHERE acc_x IS NOT NULL) AS accx_points,
                          (SELECT MIN(acc_x) FROM filtered WHERE acc_x IS NOT NULL) AS min_accx,
                          (SELECT percentile_cont(0.1) WITHIN GROUP (ORDER BY acc_x) FROM filtered WHERE acc_x IS NOT NULL) AS p10_accx,
                          (SELECT COUNT(*) FROM filtered WHERE acc_x <= -0.2) AS accx_hard_events
                        """,
                        params,
                    )
                    debug_braking = cur.fetchone() or {}
                else:
                    logger.info("Crash analysis: hard-brake source table unavailable in active schema")
                logger.info(
                    "Crash analysis braking debug (AccX): crash_ts=%s points=%s points_with_vehicle_id=%s vehicles_with_id=%s "
                    "accx_points=%s min_accx=%s p10_accx=%s accx_hard_events=%s",
                    debug_braking.get("crash_ts"),
                    debug_braking.get("points"),
                    debug_braking.get("points_with_vehicle_id"),
                    debug_braking.get("vehicles_with_id"),
                    debug_braking.get("accx_points"),
                    debug_braking.get("min_accx"),
                    debug_braking.get("p10_accx"),
                    debug_braking.get("accx_hard_events"),
                )

                # Buckets for chart
                cur.execute(
                    f"""
                    WITH crash AS (
                      SELECT
                        %(road_segment_id)s::text AS road_segment_id,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                        CASE
                          WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                          WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                            THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                          ELSE NULL
                        END AS crash_ts
                    ),
                    filtered AS (
                      SELECT
                        p.ts,
                        {cv_ctx["speed_expr"]} AS speed,
                        {cv_ctx["speed_limit_expr"]} AS speed_limit
                      {cv_ctx["from_sql"]}
                      CROSS JOIN crash c
                      WHERE {cv_ctx["geom_m_expr"]} IS NOT NULL
                        {cv_dataset_clause}
                        AND p.ts IS NOT NULL
                        AND c.crash_ts IS NOT NULL
                        AND ST_DWithin({cv_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                        AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                        {road_clause}
                    ),
                    limits AS (
                      SELECT AVG(speed_limit) AS avg_speed_limit
                      FROM filtered
                      WHERE speed_limit IS NOT NULL
                    ),
                    bucketed AS (
                      SELECT
                        CASE
                          WHEN ts >= crash_ts - interval '60 minutes' AND ts < crash_ts - interval '15 minutes' THEN '1 hr–15 min before'
                          WHEN ts >= crash_ts - interval '15 minutes' AND ts < crash_ts - interval '5 minutes' THEN '15–5 min before'
                          WHEN ts >= crash_ts - interval '5 minutes' AND ts <= crash_ts THEN '5 min to crash'
                          WHEN ts > crash_ts AND ts <= crash_ts + interval '5 minutes' THEN '0–5 min after'
                          WHEN ts > crash_ts + interval '5 minutes' AND ts <= crash_ts + interval '15 minutes' THEN '5–15 min after'
                          WHEN ts > crash_ts + interval '15 minutes' AND ts <= crash_ts + interval '60 minutes' THEN '15–60 min after'
                          ELSE NULL
                        END AS bucket,
                        speed,
                        COALESCE(speed_limit, (SELECT avg_speed_limit FROM limits)) AS eff_speed_limit
                      FROM filtered, crash
                    )
                    SELECT
                      bucket,
                      AVG(speed) AS avg_speed,
                      AVG(eff_speed_limit) AS avg_speed_limit,
                      CASE
                        WHEN bucket = '1 hr–15 min before' THEN 1
                        WHEN bucket = '15–5 min before' THEN 2
                        WHEN bucket = '5 min to crash' THEN 3
                        WHEN bucket = '0–5 min after' THEN 4
                        WHEN bucket = '5–15 min after' THEN 5
                        WHEN bucket = '15–60 min after' THEN 6
                        ELSE 99
                      END AS bucket_order
                    FROM bucketed
                    WHERE bucket IS NOT NULL
                    GROUP BY bucket
                    ORDER BY bucket_order
                    """,
                    params,
                )
                buckets = cur.fetchall()

                # Map points (sample)
                cur.execute(
                    f"""
                    WITH crash AS (
                      SELECT
                        %(road_segment_id)s::text AS road_segment_id,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                        CASE
                          WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                          WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                            THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                          ELSE NULL
                        END AS crash_ts
                    ),
                    filtered AS (
                      SELECT
                        {cv_ctx["lat_expr"]} AS latitude,
                        {cv_ctx["lon_expr"]} AS longitude,
                        p.ts AS timestamp,
                        {cv_ctx["road_segment_expr"]} AS road_segment_id,
                        {cv_ctx["road_name_expr"]} AS road_name,
                        {cv_ctx["speed_expr"]} AS speed,
                        {cv_ctx["acc_x_expr"]} AS acc_x,
                        {cv_ctx["acc_y_expr"]} AS acc_y,
                        {cv_ctx["speed_limit_expr"]} AS "speedLimit",
                        (
                          ({cv_ctx["speed_expr"]}) - ({cv_ctx["speed_limit_expr"]})
                        ) AS speed_over_limit,
                        {cv_ctx["vehicle_id_expr"]} AS vehicle_id
                      {cv_ctx["from_sql"]}
                      CROSS JOIN crash c
                      WHERE {cv_ctx["geom_m_expr"]} IS NOT NULL
                        {cv_dataset_clause}
                        AND p.ts IS NOT NULL
                        AND c.crash_ts IS NOT NULL
                        AND ST_DWithin({cv_ctx["geom_m_expr"]}, c.crash_geom_m, %(distance_m)s)
                        AND p.ts BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
                        {road_clause}
                    )
                    SELECT
                      latitude, longitude, timestamp, road_segment_id, road_name,
                      speed, "speedLimit", speed_over_limit,
                      acc_x, acc_y
                    FROM filtered
                    ORDER BY timestamp NULLS LAST
                    """,
                    params,
                )
                cv_points = cur.fetchall()

            # Workzone context (if any workzone dataset is present)
            wz_dataset_id = _latest_workzone_dataset_id(x_session_id)
            if wz_dataset_id:
                cur.execute(
                    f"""
                    WITH crash AS (
                      SELECT
                        %(road_segment_id)s::text AS road_segment_id,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%(crash_lon)s, %(crash_lat)s), 4326), 26915) AS crash_geom_m,
                        CASE
                          WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
                          WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                            THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
                          ELSE NULL
                        END AS crash_ts
                    )
                    SELECT
                      e.id,
                      e.road_segment_id,
                      e.lat,
                      e.lon,
                      e.props->>'start_date' AS start_date,
                      e.props->>'end_date' AS end_date,
                      e.props->>'geometry' AS geometry,
                      e.props->>'core_details' AS core_details
                    FROM {APP_EVENTS} e, crash c
                    WHERE e.dataset_id = %(wz_dataset_id)s
                      AND e.session_id = %(session_id)s
                      AND c.crash_ts IS NOT NULL
                      AND e.road_segment_id = c.road_segment_id
                      AND NULLIF(e.props->>'start_date','') IS NOT NULL
                      AND NULLIF(e.props->>'end_date','') IS NOT NULL
                      AND c.crash_ts BETWEEN (e.props->>'start_date')::timestamptz AND (e.props->>'end_date')::timestamptz
                      AND e.geom_m IS NOT NULL
                      AND ST_DWithin(e.geom_m, c.crash_geom_m, 500.0)
                    LIMIT 10
                    """,
                    {
                        **params,
                        "wz_dataset_id": wz_dataset_id,
                        "session_id": x_session_id,
                    },
                )
                wz_rows = cur.fetchall()
                if wz_rows:
                    workzone_lines = _make_workzone_map_payload(
                        wz_rows,
                        label="Workzones near crash",
                        exclusive=False,
                        dataset_id=wz_dataset_id,
                    ).get("lines", [])

        # Build response text
        points = summary.get("points") or 0
        vehicles = summary.get("vehicles") or 0
        avg_speed = summary.get("avg_speed")
        hb_events = braking.get("hard_braking_events") or 0
        hb_vehicles = braking.get("hard_braking_vehicles") or 0

        if rams_segment_mode:
            scope = (
                f"Crash analysis (RAMS route CV segments, +/-"
                f"{int(params.get('window_minutes', payload.window_minutes))} min"
            )
            if road_segment_id:
                scope += f", route {road_segment_id}"
            scope += "):"
        else:
            scope = (
                f"Crash analysis (CV +/-{int(params.get('window_minutes', payload.window_minutes))} min, "
                f"{float(params.get('distance_m', payload.distance_m)):.0f}m"
            )
            if road_segment_id and use_same_road:
                scope += ", same road segment"
            elif road_segment_id and not use_same_road:
                scope += ", nearby roads (no same-road matches)"
            scope += "):"
        response_lines = [scope]
        if widened_search_note:
            response_lines.append(f"- Search widened: {widened_search_note}")
        if not road_segment_id:
            response_lines.append("- Road match: not found (no nearby road segment).")
        elif crash_road_name:
            response_lines.append(f"- Road match: {crash_road_name} ({road_segment_id})")
        else:
            response_lines.append(f"- Road match: {road_segment_id}")
        if workzone_lines:
            response_lines.append(f"- Active workzones within 500m: {len(workzone_lines)}")
        else:
            response_lines.append("- Active workzones within 500m: 0")
        cv_label = "CV segment bins (5 min)" if rams_segment_mode else "CV points"
        hb_label = (
            "Hard braking (≥0.3g decel, segment aggregate)"
            if rams_segment_mode
            else "Hard braking events (<= -0.2g)"
        )
        response_lines.extend([
            f"- {cv_label}: {int(points):,}",
            f"- Vehicles: {int(vehicles):,}",
            f"- {hb_label}: {int(hb_events):,} across {int(hb_vehicles):,} vehicles",
        ])
        if avg_speed is not None:
            response_lines.append(f"- Avg speed: {avg_speed:.1f} mph")
        # Intentionally omit speed limit details from the summary response

        # Chart payload
        x_values = [b["bucket"] for b in buckets] if buckets else []
        speed_values = [float(b["avg_speed"]) if b.get("avg_speed") is not None else None for b in buckets]

        chart_payload = []
        if x_values:
            chart_payload.append({
                "type": "bar",
                "title": "Avg Speed Around Crash",
                "xLabel": "Time window (min)",
                "yLabel": "Avg speed (mph)",
                "xValues": x_values,
                "series": [
                    {"label": "Avg speed", "values": speed_values},
                ],
                "meta": {
                    "chartRole": "crash_speed_profile",
                    "description": "Average speed in time buckets around the crash.",
                },
            })

        # Map selection: crash point + CV points
        crash_point_ts = crash_ts
        if not crash_point_ts and accident_date and accident_time:
            crash_point_ts = f"{accident_date} {accident_time}"

        crash_point = {
            "latitude": payload.crash_lat,
            "longitude": payload.crash_lon,
            "type": "Crash",
            "point_type": "Crash",
            "timestamp": crash_point_ts,
            "accident_date": accident_date,
            "accident_time": accident_time,
            "severity": payload.severity,
            "road_segment_id": road_segment_id,
            "road_name": crash_road_name,
            "roadName": crash_road_name,
            "primary_id": payload.crash_id,
        }
        vehicle_points = [
            {
                **p,
                "type": "Vehicle",
                "point_type": "Traffic",
                "speedOverLimit": p.get("speed_over_limit"),
                "acceleration": {
                    "x": p.get("acc_x"),
                    "y": p.get("acc_y"),
                },
            }
            for p in cv_points
        ]
        hard_brake_points = [
            {
                **p,
                "type": "HardBrake",
                "point_type": "HardBrake",
                "speedOverLimit": p.get("speed_over_limit"),
                "decelerationG": p.get("acc_x"),
                "acceleration": {
                    "x": p.get("acc_x"),
                    "y": p.get("acc_y"),
                },
            }
            for p in cv_points
            if p.get("acc_x") is not None and float(p.get("acc_x")) <= -0.2
        ]
        map_points = [crash_point] + vehicle_points + hard_brake_points
        map_lines = ([rams_route_line] if rams_route_line else []) + workzone_lines

        response_text = "\n".join(response_lines)
        map_label = "Crash + RAMS route" if rams_segment_mode else "Crash + nearby CV points"
        result_payload = {
            "status": "success",
            "response": response_text,
            "mapSelection": {
                "label": map_label,
                "count": len(map_points),
                "points": map_points,
                "lines": map_lines,
                "overlay": False,
            },
            "chartPayload": chart_payload,
        }
        _persist_analysis_context(
            _build_crash_analysis_context(
                response_text=response_text,
                payload=payload,
                params=params,
                summary=summary,
                braking=braking,
                road_segment_id=road_segment_id,
                crash_road_name=crash_road_name,
                workzone_lines_count=len(workzone_lines),
                cv_dataset_id=cv_dataset_id,
                workzone_dataset_id=wz_dataset_id,
            ),
            session_id=x_session_id,
        )
        return result_payload
    except Exception as e:
        logger.error(f"Crash analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/area/analyze")
def analyze_area(
    payload: AreaAnalyzeRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    session_id = _require_session(x_session_id)
    try:
        polygon_json = _parse_geojson_text(payload.polygon)
        if not polygon_json:
            raise ValueError("Valid polygon GeoJSON is required.")

        cv_dataset_id = payload.cv_dataset_id or _latest_cv_dataset_id()
        exclude_unmatched = not bool(payload.include_unmatched)

        crash_dataset_id = payload.crash_dataset_id or _latest_event_dataset_id(session_id, "crash")
        workzone_dataset_id = payload.workzone_dataset_id or _latest_event_dataset_id(session_id, "workzone")
        crash_data_available = bool(crash_dataset_id)
        workzone_data_available = bool(workzone_dataset_id)
        analysis_mode = _normalize_area_analysis_mode(payload.analysis_mode)
        hard_brake_group_by = _normalize_hard_brake_group_by(payload.hard_brake_group_by)
        area_mode = "detail"
        area_km2 = 0.0
        user_id = (get_active_user() or "dev-user").strip() or "dev-user"

        summary = {}
        cv_points: list[dict] = []
        crash_points: list[dict] = []
        workzone_lines: list[dict] = []
        road_counts: list[dict] = []
        road_avg_speeds: list[dict] = []
        hard_brake_by_road: list[dict] = []
        crash_by_road: list[dict] = []
        hard_brake_by_segment: dict[str, int] = {}
        crash_by_segment: dict[str, int] = {}
        crash_counts: dict[str, int] = {}
        crash_total_count = 0
        hard_brake_points: list[dict] = []
        hard_brake_count = 0
        hard_brake_available = True
        hb_map_point_limit_used: Optional[int] = None
        fast_aggregate_mode_used = False
        area_aggregate_geojson: dict[str, Any] = {"type": "FeatureCollection", "features": []}
        area_aggregate_stats: dict[str, Any] = {"sampled": False}
        road_segments_count = 0
        map_cv_point_limit = _clamp_int(
            payload.max_map_points,
            default=_AREA_ANALYSIS_DEFAULT_MAP_POINTS,
            min_value=250,
            max_value=_AREA_ANALYSIS_MAX_MAP_POINTS_CAP,
        )
        map_hard_brake_point_limit = _clamp_int(
            payload.max_hard_brake_points,
            default=_AREA_ANALYSIS_DEFAULT_HB_MAP_POINTS,
            min_value=0,
            max_value=_AREA_ANALYSIS_MAX_HB_MAP_POINTS_CAP,
        )
        max_roads = _clamp_int(
            payload.max_roads,
            default=_AREA_ANALYSIS_DEFAULT_MAX_ROADS,
            min_value=100,
            max_value=_AREA_ANALYSIS_MAX_ROADS_CAP,
        )
        min_road_points = _clamp_int(
            payload.min_road_points,
            default=_AREA_ANALYSIS_DEFAULT_MIN_ROAD_POINTS,
            min_value=0,
            max_value=1_000_000,
        )

        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            def _relation_exists(name: str) -> bool:
                cur.execute("SELECT to_regclass(%s)", (name,))
                row = cur.fetchone()
                if not row:
                    return False
                if isinstance(row, dict):
                    return bool(row.get("to_regclass"))
                return bool(row[0])

            def _table_cols(name: str) -> set[str]:
                cur.execute(
                    """
                    SELECT a.attname
                    FROM pg_attribute a
                    WHERE a.attrelid = to_regclass(%s)
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    """,
                    (name,),
                )
                return {str(r["attname"]).lower() for r in cur.fetchall()}

            def _column_type(name: str, column: str) -> Optional[str]:
                cur.execute(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod) AS typ
                    FROM pg_attribute a
                    WHERE a.attrelid = to_regclass(%s)
                      AND a.attname = %s
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    LIMIT 1
                    """,
                    (name, column),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if isinstance(row, dict):
                    return row.get("typ")
                return row[0]

            def _norm_sql_type(type_name: Optional[str]) -> Optional[str]:
                if not type_name:
                    return None
                return re.sub(r"\s+", " ", str(type_name)).strip().lower()

            cur.execute(f"SET LOCAL statement_timeout = '{_AREA_ANALYSIS_STMT_TIMEOUT_MS}ms'")
            cur.execute(
                """
                SELECT
                  COALESCE(
                    ST_Area(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 26915)) / 1000000.0,
                    0
                  ) AS area_km2
                """,
                (polygon_json,),
            )
            area_row = cur.fetchone() or {}
            area_km2 = float(area_row.get("area_km2") or 0.0)
            if analysis_mode == "auto":
                area_mode = "aggregate" if area_km2 >= _AREA_ANALYSIS_AUTO_AGGREGATE_AREA_KM2 else "detail"
            else:
                area_mode = analysis_mode

            area_cache_key = _area_analysis_cache_key(
                user_id=user_id,
                mode=area_mode,
                cv_dataset_id=cv_dataset_id,
                crash_dataset_id=crash_dataset_id,
                workzone_dataset_id=workzone_dataset_id,
                include_unmatched=not exclude_unmatched,
                min_road_points=min_road_points,
                max_roads=max_roads,
                polygon_json=polygon_json,
            )
            if area_mode == "aggregate":
                cached_payload = _area_analysis_cache_get(area_cache_key)
                if cached_payload:
                    return cached_payload

            # Prefer active-schema CV tables first, then unqualified/public fallbacks.
            cv_table = _first_existing_relation(cur, _cv_relation_candidates(cur, "cv_points"))
            if not cv_table:
                raise ValueError("cv_points table not found.")

            cv_cols = _table_cols(cv_table)
            has_attrs = "attrs" in cv_cols
            has_cv_dataset_col = "dataset_id" in cv_cols
            if payload.cv_dataset_id and not has_cv_dataset_col:
                logger.info("Area analysis: ignoring cv_dataset_id filter because cv_points has no dataset_id column")

            cv_match_table = _first_existing_relation(cur, _cv_relation_candidates(cur, "cv_point_match"))
            cv_match_cols = _table_cols(cv_match_table) if cv_match_table else set()
            has_cv_match = bool(
                cv_match_table
                and {"point_id", "way_id"}.issubset(cv_match_cols)
                and "id" in cv_cols
            )

            road_stats_table = _first_existing_relation(
                cur,
                _cv_relation_candidates(cur, "cv_road_stats_mv")
                + _cv_relation_candidates(cur, "cv_road_agg"),
            )
            road_stats_cols = _table_cols(road_stats_table) if road_stats_table else set()
            has_road_stats = bool(road_stats_table and "way_id" in road_stats_cols)
            prefer_fast_aggregate = bool(
                area_mode == "aggregate"
                and _AREA_ANALYSIS_FAST_APPROX_ENABLED
                and area_km2 >= _AREA_ANALYSIS_FAST_APPROX_AREA_KM2
                and has_road_stats
            )

            cv_params: Dict[str, Any] = {"polygon": polygon_json}
            cv_where_parts: list[str] = []
            if has_cv_dataset_col and cv_dataset_id:
                cv_where_parts.append("p.dataset_id = %(cv_dataset_id)s")
                cv_params["cv_dataset_id"] = cv_dataset_id

            if "geom_3857" in cv_cols:
                cv_geom_3857 = "p.geom_3857"
            elif "geom" in cv_cols:
                cv_geom_3857 = "ST_Transform(p.geom, 3857)"
            elif "geom_4326" in cv_cols:
                cv_geom_3857 = "ST_Transform(p.geom_4326, 3857)"
            elif "geom_m" in cv_cols:
                cv_geom_3857 = "ST_Transform(p.geom_m, 3857)"
            else:
                raise ValueError("cv_points has no usable geometry column (geom_3857/geom/geom_4326/geom_m).")

            lat_expr = "p.lat" if "lat" in cv_cols else (
                "p.latitude" if "latitude" in cv_cols else f"ST_Y(ST_Transform({cv_geom_3857}, 4326))"
            )
            lon_expr = "p.lon" if "lon" in cv_cols else (
                "p.longitude" if "longitude" in cv_cols else f"ST_X(ST_Transform({cv_geom_3857}, 4326))"
            )

            speed_expr = "p.speed::float8" if "speed" in cv_cols else (
                "COALESCE("
                "NULLIF(p.attrs->>'speed','')::float8, "
                "NULLIF(p.attrs->>'SpeedMPH','')::float8, "
                "NULLIF(p.attrs->>'speed_mph','')::float8, "
                "NULLIF(p.attrs->>'speedMPH','')::float8"
                ")" if has_attrs else "NULL::float8"
            )

            speed_limit_parts: list[str] = []
            if has_road_stats and "speed_limit_mph" in road_stats_cols:
                speed_limit_parts.append("rs.speed_limit_mph::float8")
            if "speed_limit_mph" in cv_cols:
                speed_limit_parts.append(
                    "CASE WHEN NULLIF(p.speed_limit_mph::text,'') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN p.speed_limit_mph::float8 END"
                )
            if has_attrs:
                speed_limit_parts.extend(
                    [
                        "NULLIF(p.attrs->>'speed_limit_mph','')::float8",
                        "NULLIF(p.attrs->>'speed_limit','')::float8",
                        "NULLIF(p.attrs->>'speedlimit_mph','')::float8",
                        "NULLIF(p.attrs->>'SpeedLimitMPH','')::float8",
                        "NULLIF(p.attrs->>'speedLimit','')::float8",
                        "NULLIF(p.attrs->>'SpeedLimit','')::float8",
                    ]
                )
            speed_limit_expr = f"COALESCE({', '.join(speed_limit_parts)})" if speed_limit_parts else "NULL::float8"

            acc_x_expr = "p.acc_x::float8" if "acc_x" in cv_cols else (
                "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END"
                if has_attrs
                else "NULL::float8"
            )
            acc_y_expr = "p.acc_y::float8" if "acc_y" in cv_cols else (
                "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END"
                if has_attrs
                else "NULL::float8"
            )

            vehicle_id_expr_parts: list[str] = []
            for col in ("vehicle_id", "vehicleid", "device_id", "trip_id"):
                if col in cv_cols:
                    vehicle_id_expr_parts.append(f"NULLIF(TRIM(p.{col}::text),'')")
            if has_attrs:
                vehicle_id_expr_parts.extend(
                    [
                        "NULLIF(TRIM(p.attrs->>'VehicleID'),'')",
                        "NULLIF(TRIM(p.attrs->>'vehicle_id'),'')",
                        "NULLIF(TRIM(p.attrs->>'vehicleId'),'')",
                        "NULLIF(TRIM(p.attrs->>'vehicleid'),'')",
                        "NULLIF(TRIM(p.attrs->>'TripID'),'')",
                        "NULLIF(TRIM(p.attrs->>'trip_id'),'')",
                        "NULLIF(TRIM(p.attrs->>'tripId'),'')",
                        "NULLIF(TRIM(p.attrs->>'DeviceID'),'')",
                        "NULLIF(TRIM(p.attrs->>'device_id'),'')",
                        "NULLIF(TRIM(p.attrs->>'deviceId'),'')",
                    ]
                )
            vehicle_id_expr = (
                f"COALESCE({', '.join(vehicle_id_expr_parts)})"
                if vehicle_id_expr_parts
                else "NULL::text"
            )

            cv_id_type = _column_type(cv_table, "id") if "id" in cv_cols else None
            cv_way_id_type = _column_type(cv_table, "way_id") if "way_id" in cv_cols else None
            cv_match_point_id_type = _column_type(cv_match_table, "point_id") if has_cv_match else None
            cv_match_way_id_type = _column_type(cv_match_table, "way_id") if has_cv_match else None
            road_stats_way_id_type = _column_type(road_stats_table, "way_id") if has_road_stats else None

            cv_match_join_condition = "m.point_id::text = p.id::text"
            if has_cv_match and _norm_sql_type(cv_id_type) == _norm_sql_type(cv_match_point_id_type):
                cv_match_join_condition = "m.point_id = p.id"
            cv_match_join_sql = f"LEFT JOIN {cv_match_table} m ON {cv_match_join_condition}" if has_cv_match else ""

            way_id_terms: list[str] = []
            if "way_id" in cv_cols:
                way_id_terms.append("p.way_id::text")
            if has_cv_match:
                way_id_terms.append("m.way_id::text")

            road_join_key_expr: Optional[str] = None
            road_join_key_type: Optional[str] = None
            if "way_id" in cv_cols and has_cv_match:
                if _norm_sql_type(cv_way_id_type) == _norm_sql_type(cv_match_way_id_type):
                    road_join_key_expr = "COALESCE(p.way_id, m.way_id)"
                    road_join_key_type = cv_way_id_type
                else:
                    road_join_key_expr = "p.way_id"
                    road_join_key_type = cv_way_id_type
            elif "way_id" in cv_cols:
                road_join_key_expr = "p.way_id"
                road_join_key_type = cv_way_id_type
            elif has_cv_match:
                road_join_key_expr = "m.way_id"
                road_join_key_type = cv_match_way_id_type

            road_stats_join_sql = ""
            if has_road_stats and road_join_key_expr:
                road_stats_join_sql = (
                    f"LEFT JOIN {road_stats_table} rs "
                    f"ON NULLIF(TRIM((rs.way_id)::text), '') = NULLIF(TRIM(({road_join_key_expr})::text), '')"
                )

            road_segment_terms: list[str] = []
            # Prefer way_id so aggregate-mode overlap can be keyed on a stable road id.
            if "way_id" in cv_cols:
                road_segment_terms.append("p.way_id::text")
            if has_cv_match:
                road_segment_terms.append("m.way_id::text")
            if "road_segment_id" in cv_cols:
                road_segment_terms.append("p.road_segment_id::text")
            road_segment_expr = f"COALESCE({', '.join(road_segment_terms)})" if road_segment_terms else "NULL::text"

            if exclude_unmatched:
                cv_where_parts.append(f"{road_segment_expr} IS NOT NULL")

            road_name_fallback_parts: list[str] = []
            if has_attrs:
                road_name_fallback_parts.append(_attrs_road_name_expr("p.attrs"))
            if "road_name" in cv_cols:
                road_name_fallback_parts.append("NULLIF(p.road_name::text,'')")
            if "name" in cv_cols:
                road_name_fallback_parts.append("NULLIF(p.name::text,'')")
            area_road_stats_name_kwargs = _road_stats_name_kwargs(road_stats_cols) if has_road_stats else {}
            road_name_expr = _preferred_road_name_expr(
                **area_road_stats_name_kwargs,
                extra_exprs=road_name_fallback_parts,
            )

            cv_where_parts.extend(
                [
                    f"{cv_geom_3857} IS NOT NULL",
                    f"ST_Intersects({cv_geom_3857}, poly.geom_3857)",
                ]
            )
            cv_where_sql = " AND ".join(cv_where_parts)

            cv_base_sql = f"""
                WITH poly AS (
                  SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                ),
                base AS (
                  SELECT
                    p.ts AS ts,
                    {lat_expr} AS latitude,
                    {lon_expr} AS longitude,
                    {road_segment_expr} AS road_segment_id,
                    {road_name_expr} AS road_name,
                    {speed_expr} AS speed,
                    {vehicle_id_expr} AS vehicle_id,
                    {acc_x_expr} AS acc_x,
                    {acc_y_expr} AS acc_y,
                    {speed_limit_expr} AS speed_limit_mph
                  FROM {cv_table} p
                  {cv_match_join_sql}
                  {road_stats_join_sql}
                  CROSS JOIN poly
                  WHERE {cv_where_sql}
                )
            """

            if area_mode == "detail":
                cur.execute(
                    cv_base_sql
                    + """
                    SELECT
                      COUNT(*) AS points,
                      COUNT(DISTINCT vehicle_id) AS vehicles,
                      AVG(speed) AS avg_speed,
                      MIN(speed) AS min_speed,
                      MAX(speed) AS max_speed,
                      MIN(ts) AS min_ts,
                      MAX(ts) AS max_ts,
                      COUNT(*) FILTER (WHERE speed_limit_mph IS NOT NULL AND speed_limit_mph > 0) AS limit_points,
                      COUNT(*) FILTER (WHERE speed IS NOT NULL AND speed_limit_mph IS NOT NULL AND speed_limit_mph > 0 AND (speed - speed_limit_mph) > 10) AS speeding_points,
                      COUNT(*) FILTER (WHERE speed IS NOT NULL AND speed_limit_mph IS NOT NULL AND speed_limit_mph > 0 AND (speed - speed_limit_mph) < -10) AS under_points
                    FROM base
                    """,
                    cv_params,
                )
                summary = cur.fetchone() or {}

                cur.execute(
                    cv_base_sql
                    + """
                    SELECT
                      ts AS timestamp,
                      latitude,
                      longitude,
                      road_segment_id,
                      road_name,
                      speed,
                      acc_x,
                      acc_y,
                      speed_limit_mph
                    FROM base
                    ORDER BY ts NULLS LAST
                    LIMIT %(map_cv_point_limit)s
                    """,
                    {**cv_params, "map_cv_point_limit": map_cv_point_limit},
                )
                cv_points = cur.fetchall()

                cur.execute(
                    cv_base_sql
                    + """
                    SELECT
                      road_name,
                      COUNT(*) AS count
                    FROM base
                    WHERE road_name IS NOT NULL
                    GROUP BY 1
                    ORDER BY count DESC
                    LIMIT 5
                    """,
                    cv_params,
                )
                road_counts = cur.fetchall()

                cur.execute(
                    cv_base_sql
                    + """
                    SELECT
                      road_name,
                      AVG(speed) AS avg_speed,
                      COUNT(*) AS count
                    FROM base
                    WHERE road_name IS NOT NULL
                    GROUP BY 1
                    ORDER BY count DESC
                    LIMIT 8
                    """,
                    cv_params,
                )
                road_avg_speeds = cur.fetchall()
            else:
                def _pick_road_col(options: list[str], cols: set[str]) -> Optional[str]:
                    for col in options:
                        if col in cols:
                            return col
                    return None

                geom_source_candidates: list[str] = []
                if road_stats_table:
                    geom_source_candidates.append(road_stats_table)
                geom_source_candidates.extend(
                    _cv_relation_candidates(cur, "viz_matched_roads_tbl")
                    + _cv_relation_candidates(cur, "roads")
                )
                agg_geom_source_table: Optional[str] = None
                agg_geom_source_cols: set[str] = set()
                agg_geom_col: Optional[str] = None
                agg_geom_id_col: Optional[str] = None
                for candidate in geom_source_candidates:
                    if not candidate:
                        continue
                    if not _relation_exists(candidate):
                        continue
                    cols = _table_cols(candidate)
                    candidate_geom_col = _pick_road_col(["geom_3857", "geom", "geom_4326", "geom_m"], cols)
                    candidate_id_col = _pick_road_col(["way_id", "road_segment_id", "segment_id", "road_id"], cols)
                    if candidate_geom_col and candidate_id_col:
                        agg_geom_source_table = candidate
                        agg_geom_source_cols = cols
                        agg_geom_col = candidate_geom_col
                        agg_geom_id_col = candidate_id_col
                        break
                if not agg_geom_source_table or not agg_geom_col or not agg_geom_id_col:
                    raise ValueError("No geometry source table found for area aggregate mode.")

                agg_geom_3857_expr = (
                    f"g.{agg_geom_col}"
                    if agg_geom_col == "geom_3857"
                    else f"ST_Transform(g.{agg_geom_col}, 3857)"
                )
                agg_geom_where_parts = [f"g.{agg_geom_col} IS NOT NULL"]
                if "dataset_id" in agg_geom_source_cols and cv_dataset_id:
                    agg_geom_where_parts.append("g.dataset_id = %(cv_dataset_id)s")
                agg_geom_where_sql = " AND ".join(agg_geom_where_parts)

                use_fast_stats_aggregate = False
                stats_id_col: Optional[str] = None
                if prefer_fast_aggregate and road_stats_table:
                    stats_id_col = _pick_road_col(["way_id", "road_segment_id", "segment_id", "road_id"], road_stats_cols)
                    use_fast_stats_aggregate = bool(stats_id_col)
                    fast_aggregate_mode_used = use_fast_stats_aggregate

                agg_query_prefix_sql = cv_base_sql
                vehicle_summary_sql = "COALESCE((SELECT COUNT(DISTINCT vehicle_id) FROM filtered_base), 0) AS vehicles,"
                fast_overlap_geom_expr = "poly.geom_3857"
                fast_overlap_simplified = False

                if use_fast_stats_aggregate and road_stats_table and stats_id_col:
                    logger.info(
                        "Area analysis aggregate fast-mode enabled: area_km2=%.1f threshold=%.1f dataset_id=%s",
                        area_km2,
                        _AREA_ANALYSIS_FAST_APPROX_AREA_KM2,
                        cv_dataset_id,
                    )
                    stats_ref_terms = [f"NULLIF(rs.{c}::text,'')" for c in ("ref", "route", "highway_ref", "highwayref") if c in road_stats_cols]
                    stats_ref_expr = f"COALESCE({', '.join(stats_ref_terms)})" if stats_ref_terms else None
                    stats_name_terms = [f"NULLIF(rs.{c}::text,'')" for c in ("label", "name", "road_name") if c in road_stats_cols]
                    stats_name_expr = f"COALESCE({', '.join(stats_name_terms)})" if stats_name_terms else None
                    stats_highway_expr = (
                        "initcap(replace(rs.highway::text, '_', ' '))"
                        if "highway" in road_stats_cols
                        else None
                    )
                    stats_road_name_expr = _preferred_road_name_expr(
                        ref_expr=stats_ref_expr,
                        name_expr=stats_name_expr,
                        highway_expr=stats_highway_expr,
                    )
                    stats_avg_speed_col = _pick_road_col(["avg_speed_mph", "avg_speed", "speed_mph", "speed"], road_stats_cols)
                    stats_min_speed_col = _pick_road_col(["min_speed_mph", "min_speed"], road_stats_cols)
                    stats_max_speed_col = _pick_road_col(["max_speed_mph", "max_speed"], road_stats_cols)
                    stats_limit_col = _pick_road_col(["speed_limit_mph", "speed_limit"], road_stats_cols)
                    stats_start_col = _pick_road_col(["start_ts", "min_ts", "start_time"], road_stats_cols)
                    stats_end_col = _pick_road_col(["end_ts", "max_ts", "end_time"], road_stats_cols)
                    stats_unique_vehicle_col = _pick_road_col(
                        ["unique_vehicles_total", "vehicle_count", "vehicles"],
                        road_stats_cols,
                    )
                    stats_avg_unique_vehicle_hour_col = _pick_road_col(
                        ["avg_unique_vehicles_per_hour"],
                        road_stats_cols,
                    )
                    stats_hourly_unique_vehicle_json_col = _pick_road_col(
                        ["hourly_unique_vehicles_json"],
                        road_stats_cols,
                    )
                    point_count_expr = (
                        "GREATEST(COALESCE(rs.point_count::bigint, 0), 0)"
                        if "point_count" in road_stats_cols
                        else "1::bigint"
                    )
                    avg_speed_expr = f"rs.{stats_avg_speed_col}::float8" if stats_avg_speed_col else "NULL::float8"
                    min_speed_expr = f"rs.{stats_min_speed_col}::float8" if stats_min_speed_col else avg_speed_expr
                    max_speed_expr = f"rs.{stats_max_speed_col}::float8" if stats_max_speed_col else avg_speed_expr
                    speed_limit_expr = f"rs.{stats_limit_col}::float8" if stats_limit_col else "NULL::float8"
                    limit_points_expr = (
                        "GREATEST(COALESCE(rs.limit_points::bigint, 0), 0)"
                        if "limit_points" in road_stats_cols
                        else (
                            point_count_expr
                            if stats_limit_col
                            else "0::bigint"
                        )
                    )
                    speeding_points_expr = (
                        "GREATEST(COALESCE(rs.speeding_points::bigint, 0), 0)"
                        if "speeding_points" in road_stats_cols
                        else (
                            "GREATEST(COALESCE(rs.over_limit_points::bigint, 0), 0)"
                            if "over_limit_points" in road_stats_cols
                            else "0::bigint"
                        )
                    )
                    under_points_expr = (
                        "GREATEST(COALESCE(rs.under_points::bigint, 0), 0)"
                        if "under_points" in road_stats_cols
                        else (
                            "GREATEST(COALESCE(rs.below_limit_points::bigint, 0), 0)"
                            if "below_limit_points" in road_stats_cols
                            else "0::bigint"
                        )
                    )
                    unique_vehicle_count_expr = (
                        f"GREATEST(COALESCE(rs.{stats_unique_vehicle_col}::bigint, 0), 0)"
                        if stats_unique_vehicle_col
                        else "0::bigint"
                    )
                    avg_unique_vehicle_hour_expr = (
                        f"rs.{stats_avg_unique_vehicle_hour_col}::float8"
                        if stats_avg_unique_vehicle_hour_col
                        else "NULL::float8"
                    )
                    hourly_unique_vehicle_json_expr = (
                        f"COALESCE(rs.{stats_hourly_unique_vehicle_json_col}, '{{}}'::jsonb)"
                        if stats_hourly_unique_vehicle_json_col
                        else "'{}'::jsonb"
                    )
                    start_ts_expr = f"rs.{stats_start_col}" if stats_start_col else "NULL::timestamptz"
                    end_ts_expr = f"rs.{stats_end_col}" if stats_end_col else "NULL::timestamptz"
                    road_stats_where_parts = ["1=1"]
                    if "dataset_id" in road_stats_cols and cv_dataset_id:
                        road_stats_where_parts.append("rs.dataset_id = %(cv_dataset_id)s")
                    road_stats_where_sql = " AND ".join(road_stats_where_parts)
                    agg_geom_id_type = _column_type(agg_geom_source_table, agg_geom_id_col)
                    stats_id_type = _column_type(road_stats_table, stats_id_col)
                    typed_overlap_join = _norm_sql_type(agg_geom_id_type) == _norm_sql_type(stats_id_type)
                    overlap_key_expr = (
                        f"g.{agg_geom_id_col}"
                        if typed_overlap_join
                        else f"g.{agg_geom_id_col}::text"
                    )
                    stats_key_expr = (
                        f"rs.{stats_id_col}"
                        if typed_overlap_join
                        else f"rs.{stats_id_col}::text"
                    )
                    if (
                        _AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_TOLERANCE_M > 0
                        and area_km2 >= _AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_MIN_AREA_KM2
                    ):
                        fast_overlap_geom_expr = (
                            "ST_SimplifyPreserveTopology("
                            "poly.geom_3857, %(poly_simplify_tolerance_m)s)"
                        )
                        fast_overlap_simplified = True
                    agg_query_prefix_sql = """
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                        )
                    """
                    vehicle_summary_sql = "COALESCE((SELECT SUM(vehicle_count) FROM agg), 0) AS vehicles,"
                    agg_cte_sql = f"""
                    ,
                    overlap_segments AS (
                      SELECT DISTINCT
                        {overlap_key_expr} AS overlap_key,
                        g.{agg_geom_id_col}::text AS road_segment_id
                      FROM {agg_geom_source_table} g
                      JOIN poly ON TRUE
                      WHERE {agg_geom_where_sql}
                        AND ({agg_geom_3857_expr}) && ST_Expand({fast_overlap_geom_expr}, %(segment_overlap_tolerance_m)s)
                        AND ST_DWithin(({agg_geom_3857_expr}), {fast_overlap_geom_expr}, %(segment_overlap_tolerance_m)s)
                    ),
                    agg_stats AS (
                      SELECT
                        {stats_key_expr} AS overlap_key,
                        rs.{stats_id_col}::text AS road_segment_id,
                        {stats_road_name_expr} AS road_name,
                        {point_count_expr} AS point_count,
                        {avg_speed_expr} AS avg_speed_mph,
                        {min_speed_expr} AS min_speed_mph,
                        {max_speed_expr} AS max_speed_mph,
                        {start_ts_expr} AS start_ts,
                        {end_ts_expr} AS end_ts,
                        {speed_limit_expr} AS speed_limit_mph,
                        {limit_points_expr} AS limit_points,
                        {speeding_points_expr} AS speeding_points,
                        {under_points_expr} AS under_points,
                        {unique_vehicle_count_expr} AS unique_vehicles_total,
                        {avg_unique_vehicle_hour_expr} AS avg_unique_vehicles_per_hour,
                        {hourly_unique_vehicle_json_expr} AS hourly_unique_vehicles_json
                      FROM {road_stats_table} rs
                      WHERE {road_stats_where_sql}
                    ),
                    agg AS (
                      SELECT
                        s.road_segment_id,
                        COALESCE(NULLIF(s.road_name, ''), '[unknown]') AS road_name,
                        SUM(s.point_count)::bigint AS point_count,
                        CASE
                          WHEN COALESCE(SUM(s.point_count), 0) > 0
                            THEN SUM(COALESCE(s.avg_speed_mph, 0.0) * s.point_count) / NULLIF(SUM(s.point_count), 0)
                          ELSE AVG(s.avg_speed_mph)
                        END AS avg_speed_mph,
                        MIN(s.min_speed_mph) AS min_speed_mph,
                        MAX(s.max_speed_mph) AS max_speed_mph,
                        MIN(s.start_ts) AS start_ts,
                        MAX(s.end_ts) AS end_ts,
                        CASE
                          WHEN COALESCE(SUM(s.limit_points), 0) > 0
                            THEN SUM(COALESCE(s.speed_limit_mph, 0.0) * s.limit_points) / NULLIF(SUM(s.limit_points), 0)
                          ELSE AVG(s.speed_limit_mph)
                        END AS speed_limit_mph,
                        SUM(s.limit_points)::bigint AS limit_points,
                        SUM(s.speeding_points)::bigint AS speeding_points,
                        SUM(s.under_points)::bigint AS under_points,
                        SUM(s.unique_vehicles_total)::bigint AS vehicle_count,
                        CASE
                          WHEN COALESCE(SUM(s.point_count), 0) > 0
                            THEN SUM(COALESCE(s.avg_unique_vehicles_per_hour, 0.0) * s.point_count) / NULLIF(SUM(s.point_count), 0)
                          ELSE AVG(s.avg_unique_vehicles_per_hour)
                        END AS avg_unique_vehicles_per_hour,
                        COALESCE((MIN(s.hourly_unique_vehicles_json::text))::jsonb, '{{}}'::jsonb) AS hourly_unique_vehicles_json
                      FROM agg_stats s
                      JOIN overlap_segments os
                        ON os.overlap_key = s.overlap_key
                      WHERE s.road_segment_id IS NOT NULL
                      GROUP BY 1, 2
                      HAVING SUM(s.point_count) >= %(min_road_points)s
                    )
                    """
                else:
                    # Aggregate strictly from CV points inside the polygon.
                    fast_aggregate_mode_used = False
                    agg_cte_sql = f"""
                    ,
                    overlap_segments AS (
                      SELECT DISTINCT g.{agg_geom_id_col}::text AS road_segment_id
                      FROM {agg_geom_source_table} g
                      JOIN poly ON TRUE
                      WHERE {agg_geom_where_sql}
                        AND ({agg_geom_3857_expr}) && ST_Expand(poly.geom_3857, %(segment_overlap_tolerance_m)s)
                        AND ST_DWithin(({agg_geom_3857_expr}), poly.geom_3857, %(segment_overlap_tolerance_m)s)
                    ),
                    filtered_base AS (
                      SELECT b.*
                      FROM base b
                      WHERE b.road_segment_id IS NOT NULL
                        AND b.road_segment_id IN (SELECT road_segment_id FROM overlap_segments)
                    ),
                    road_hourly AS (
                      SELECT
                        road_segment_id,
                        TO_CHAR(date_trunc('hour', ts), 'HH24') AS hour_key,
                        COUNT(DISTINCT vehicle_id)::float8 AS unique_vehicles
                      FROM filtered_base
                      WHERE ts IS NOT NULL
                        AND vehicle_id IS NOT NULL
                        AND vehicle_id <> ''
                      GROUP BY 1, 2
                    ),
                    road_hourly_json AS (
                      SELECT
                        road_segment_id,
                        jsonb_object_agg(hour_key, unique_vehicles ORDER BY hour_key) AS hourly_unique_vehicles_json
                      FROM road_hourly
                      GROUP BY 1
                    ),
                    agg_base AS (
                      SELECT
                        road_segment_id,
                        COALESCE(NULLIF(road_name, ''), '[unknown]') AS road_name,
                        COUNT(*) AS point_count,
                        AVG(speed) AS avg_speed_mph,
                        MIN(speed) AS min_speed_mph,
                        MAX(speed) AS max_speed_mph,
                        MIN(ts) AS start_ts,
                        MAX(ts) AS end_ts,
                        AVG(speed_limit_mph) FILTER (WHERE speed_limit_mph IS NOT NULL AND speed_limit_mph > 0) AS speed_limit_mph,
                        COUNT(*) FILTER (WHERE speed_limit_mph IS NOT NULL AND speed_limit_mph > 0) AS limit_points,
                        COUNT(*) FILTER (
                          WHERE speed IS NOT NULL
                            AND speed_limit_mph IS NOT NULL
                            AND speed_limit_mph > 0
                            AND (speed - speed_limit_mph) > 10
                        ) AS speeding_points,
                        COUNT(*) FILTER (
                          WHERE speed IS NOT NULL
                            AND speed_limit_mph IS NOT NULL
                            AND speed_limit_mph > 0
                            AND (speed - speed_limit_mph) < -10
                        ) AS under_points,
                        COUNT(DISTINCT vehicle_id) FILTER (
                          WHERE vehicle_id IS NOT NULL AND vehicle_id <> ''
                        ) AS vehicle_count,
                        CASE
                          WHEN COUNT(DISTINCT date_trunc('hour', ts)) FILTER (WHERE ts IS NOT NULL) > 0
                            THEN (
                              COUNT(DISTINCT vehicle_id) FILTER (
                                WHERE vehicle_id IS NOT NULL AND vehicle_id <> ''
                              )::float8
                              / NULLIF(
                                  (COUNT(DISTINCT date_trunc('hour', ts)) FILTER (WHERE ts IS NOT NULL))::float8,
                                  0
                                )
                            )
                          ELSE NULL::float8
                        END AS avg_unique_vehicles_per_hour
                      FROM filtered_base
                      GROUP BY 1, 2
                      HAVING COUNT(*) >= %(min_road_points)s
                    ),
                    agg AS (
                      SELECT
                        ab.*,
                        COALESCE(rh.hourly_unique_vehicles_json, '{{}}'::jsonb) AS hourly_unique_vehicles_json
                      FROM agg_base ab
                      LEFT JOIN road_hourly_json rh
                        ON rh.road_segment_id = ab.road_segment_id
                    )
                    """

                road_params: Dict[str, Any] = {
                    **cv_params,
                    "max_roads": max_roads,
                    "min_road_points": min_road_points,
                    "segment_overlap_tolerance_m": _AREA_ANALYSIS_SEGMENT_OVERLAP_TOLERANCE_M,
                }
                if cv_dataset_id:
                    road_params.setdefault("cv_dataset_id", cv_dataset_id)
                if use_fast_stats_aggregate and fast_overlap_simplified:
                    road_params["poly_simplify_tolerance_m"] = _AREA_ANALYSIS_FAST_OVERLAP_SIMPLIFY_TOLERANCE_M

                area_agg_tmp_table = "area_agg_tmp"
                if use_fast_stats_aggregate:
                    t_agg_materialize_start = time.perf_counter()
                    cur.execute(f"DROP TABLE IF EXISTS {area_agg_tmp_table}")
                    cur.execute(
                        "CREATE TEMP TABLE area_agg_tmp ON COMMIT DROP AS "
                        + agg_query_prefix_sql
                        + agg_cte_sql
                        + " SELECT * FROM agg",
                        road_params,
                    )
                    logger.info(
                        "Area analysis aggregate stage=materialize mode=fast area_km2=%.1f simplify=%s duration_ms=%.1f",
                        area_km2,
                        fast_overlap_simplified,
                        (time.perf_counter() - t_agg_materialize_start) * 1000.0,
                    )

                    t_summary_start = time.perf_counter()
                    cur.execute(
                        f"""
                        SELECT
                          COALESCE(COUNT(*), 0) AS road_segments,
                          COALESCE(SUM(point_count), 0) AS points,
                          COALESCE(SUM(vehicle_count), 0) AS vehicles,
                          CASE
                            WHEN COALESCE(SUM(point_count), 0) > 0
                              THEN SUM(avg_speed_mph * point_count) / NULLIF(SUM(point_count), 0)
                            ELSE NULL::float8
                          END AS avg_speed,
                          MIN(min_speed_mph) AS min_speed,
                          MAX(max_speed_mph) AS max_speed,
                          MIN(start_ts) AS min_ts,
                          MAX(end_ts) AS max_ts,
                          COALESCE(SUM(limit_points), 0) AS limit_points,
                          COALESCE(SUM(speeding_points), 0) AS speeding_points,
                          COALESCE(SUM(under_points), 0) AS under_points,
                          CASE
                            WHEN COALESCE(SUM(point_count), 0) > 0
                              THEN SUM(COALESCE(avg_unique_vehicles_per_hour, 0.0) * point_count) / NULLIF(SUM(point_count), 0)
                            ELSE NULL::float8
                          END AS avg_unique_vehicles_per_hour,
                          (
                            SELECT COALESCE(jsonb_object_agg(hour_key, hour_total ORDER BY hour_key), '{{}}'::jsonb)
                            FROM (
                              SELECT
                                h.key AS hour_key,
                                SUM(NULLIF(h.value, '')::float8) AS hour_total
                              FROM {area_agg_tmp_table} agg_hourly
                              CROSS JOIN LATERAL jsonb_each_text(COALESCE(agg_hourly.hourly_unique_vehicles_json, '{{}}'::jsonb)) h
                              GROUP BY h.key
                            ) hourly_totals
                          ) AS hourly_unique_vehicles
                        FROM {area_agg_tmp_table}
                        """
                    )
                    summary = cur.fetchone() or {}
                    road_segments_count = int(summary.get("road_segments") or 0)
                    logger.info(
                        "Area analysis aggregate stage=summary mode=fast roads=%d duration_ms=%.1f",
                        road_segments_count,
                        (time.perf_counter() - t_summary_start) * 1000.0,
                    )
                else:
                    cur.execute(
                        agg_query_prefix_sql
                        + agg_cte_sql
                        + f"""
                        SELECT
                          COALESCE((SELECT COUNT(*) FROM agg), 0) AS road_segments,
                          COALESCE((SELECT SUM(point_count) FROM agg), 0) AS points,
                          {vehicle_summary_sql}
                          (
                            SELECT
                              CASE
                                WHEN COALESCE(SUM(point_count), 0) > 0
                                  THEN SUM(avg_speed_mph * point_count) / NULLIF(SUM(point_count), 0)
                                ELSE NULL::float8
                              END
                            FROM agg
                          ) AS avg_speed,
                          (SELECT MIN(min_speed_mph) FROM agg) AS min_speed,
                          (SELECT MAX(max_speed_mph) FROM agg) AS max_speed,
                          (SELECT MIN(start_ts) FROM agg) AS min_ts,
                          (SELECT MAX(end_ts) FROM agg) AS max_ts,
                          COALESCE((SELECT SUM(limit_points) FROM agg), 0) AS limit_points,
                          COALESCE((SELECT SUM(speeding_points) FROM agg), 0) AS speeding_points,
                          COALESCE((SELECT SUM(under_points) FROM agg), 0) AS under_points,
                          (
                            SELECT
                              CASE
                                WHEN COALESCE(SUM(point_count), 0) > 0
                                  THEN SUM(COALESCE(avg_unique_vehicles_per_hour, 0.0) * point_count) / NULLIF(SUM(point_count), 0)
                                ELSE NULL::float8
                              END
                            FROM agg
                          ) AS avg_unique_vehicles_per_hour,
                          (
                            SELECT COALESCE(jsonb_object_agg(hour_key, hour_total ORDER BY hour_key), '{{}}'::jsonb)
                            FROM (
                              SELECT
                                h.key AS hour_key,
                                SUM(NULLIF(h.value, '')::float8) AS hour_total
                              FROM agg agg_hourly
                              CROSS JOIN LATERAL jsonb_each_text(COALESCE(agg_hourly.hourly_unique_vehicles_json, '{{}}'::jsonb)) h
                              GROUP BY h.key
                            ) hourly_totals
                          ) AS hourly_unique_vehicles
                        """,
                        road_params,
                    )
                    summary = cur.fetchone() or {}
                    road_segments_count = int(summary.get("road_segments") or 0)

                logger.info(
                    "Area analysis aggregate stage=unique_metrics source=aggregate_summary mode=%s",
                    "fast" if use_fast_stats_aggregate else "full",
                )
                cv_points = []

                geom_source_candidates = []
                if road_stats_table:
                    geom_source_candidates.append(road_stats_table)
                geom_source_candidates.extend(
                    _cv_relation_candidates(cur, "viz_matched_roads_tbl")
                    + _cv_relation_candidates(cur, "roads")
                )
                geom_source_table: Optional[str] = None
                geom_source_cols: set[str] = set()
                geom_col: Optional[str] = None
                geom_id_col: Optional[str] = None
                for candidate in geom_source_candidates:
                    if not candidate:
                        continue
                    if not _relation_exists(candidate):
                        continue
                    cols = _table_cols(candidate)
                    candidate_geom_col = _pick_road_col(["geom_3857", "geom", "geom_4326", "geom_m"], cols)
                    candidate_id_col = _pick_road_col(["way_id", "road_segment_id", "segment_id", "road_id"], cols)
                    if candidate_geom_col and candidate_id_col:
                        geom_source_table = candidate
                        geom_source_cols = cols
                        geom_col = candidate_geom_col
                        geom_id_col = candidate_id_col
                        break
                if not geom_source_table or not geom_col or not geom_id_col:
                    raise ValueError("No geometry source table found for area aggregate mode.")

                geom_3857_expr = (
                    f"g.{geom_col}"
                    if geom_col == "geom_3857"
                    else (
                        f"ST_Transform(g.{geom_col}, 3857)"
                        if geom_col in {"geom", "geom_4326"}
                        else f"ST_Transform(g.{geom_col}, 3857)"
                    )
                )
                geom_ref_terms = [f"NULLIF(g.{c}::text,'')" for c in ("ref", "route", "highway_ref", "highwayRef") if c in geom_source_cols]
                geom_ref_expr = f"COALESCE({', '.join(geom_ref_terms)})" if geom_ref_terms else None
                geom_name_terms = [f"NULLIF(g.{c}::text,'')" for c in ("road_name", "name") if c in geom_source_cols]
                geom_name_text_expr = f"COALESCE({', '.join(geom_name_terms)})" if geom_name_terms else None
                geom_label_expr = "g.label::text" if "label" in geom_source_cols else None
                geom_highway_expr = None
                if "highway" in geom_source_cols:
                    geom_highway_expr = (
                        "CASE WHEN NULLIF(g.highway::text,'') IS NOT NULL THEN "
                        "initcap(replace(g.highway::text, '_', ' ')) "
                        + ("|| ' #' || g.way_id::text " if "way_id" in geom_source_cols else "")
                        + "ELSE NULL END"
                    )
                geom_name_expr = _preferred_road_name_expr(
                    ref_expr=geom_ref_expr,
                    label_expr=geom_label_expr,
                    name_expr=geom_name_text_expr,
                    highway_expr=geom_highway_expr,
                )
                geom_where_parts = [f"g.{geom_col} IS NOT NULL"]
                if "dataset_id" in geom_source_cols and cv_dataset_id:
                    geom_where_parts.append("g.dataset_id = %(cv_dataset_id)s")
                geom_where_sql = " AND ".join(geom_where_parts)

                t_geometry_start = time.perf_counter()
                if use_fast_stats_aggregate:
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                        ),
                        agg_limited AS (
                          SELECT *
                          FROM {area_agg_tmp_table}
                          ORDER BY point_count DESC NULLS LAST
                          LIMIT %(max_roads)s
                        ),
                        geom_src AS (
                          SELECT
                            g.{geom_id_col}::text AS geom_road_segment_id,
                            {geom_name_expr} AS geom_road_name,
                            {geom_3857_expr} AS geom_3857
                          FROM {geom_source_table} g
                          JOIN agg_limited al
                            ON g.{geom_id_col}::text = al.road_segment_id
                          WHERE {geom_where_sql}
                        )
                        SELECT
                          a.road_segment_id,
                          COALESCE(NULLIF(a.road_name,''), NULLIF(gs.geom_road_name,''), '[unknown]') AS road_name,
                          a.point_count,
                          a.avg_speed_mph,
                          a.speed_limit_mph,
                          a.min_speed_mph,
                          a.max_speed_mph,
                          a.start_ts,
                          a.end_ts,
                          a.vehicle_count AS unique_vehicles_total,
                          a.avg_unique_vehicles_per_hour,
                          a.hourly_unique_vehicles_json,
                          ST_AsGeoJSON(ST_Transform(gs.geom_3857, 4326)) AS geom_json
                        FROM agg_limited a
                        JOIN poly ON TRUE
                        LEFT JOIN geom_src gs
                          ON gs.geom_road_segment_id = a.road_segment_id
                        WHERE gs.geom_3857 IS NOT NULL
                          AND gs.geom_3857 && poly.geom_3857
                          AND ST_Intersects(gs.geom_3857, poly.geom_3857)
                        ORDER BY a.point_count DESC NULLS LAST
                        """,
                        road_params,
                    )
                else:
                    cur.execute(
                        agg_query_prefix_sql
                        + agg_cte_sql
                        + f"""
                        ,
                        agg_limited AS (
                          SELECT *
                          FROM agg
                          ORDER BY point_count DESC NULLS LAST
                          LIMIT %(max_roads)s
                        ),
                        geom_src AS (
                          SELECT
                            g.{geom_id_col}::text AS geom_road_segment_id,
                            {geom_name_expr} AS geom_road_name,
                            {geom_3857_expr} AS geom_3857
                          FROM {geom_source_table} g
                          JOIN agg_limited al
                            ON g.{geom_id_col}::text = al.road_segment_id
                          WHERE {geom_where_sql}
                        )
                        SELECT
                          a.road_segment_id,
                          COALESCE(NULLIF(a.road_name,''), NULLIF(gs.geom_road_name,''), '[unknown]') AS road_name,
                          a.point_count,
                          a.avg_speed_mph,
                          a.speed_limit_mph,
                          a.min_speed_mph,
                          a.max_speed_mph,
                          a.start_ts,
                          a.end_ts,
                          a.vehicle_count AS unique_vehicles_total,
                          a.avg_unique_vehicles_per_hour,
                          a.hourly_unique_vehicles_json,
                          ST_AsGeoJSON(ST_Transform(gs.geom_3857, 4326)) AS geom_json
                        FROM agg_limited a
                        JOIN poly ON TRUE
                        LEFT JOIN geom_src gs
                          ON gs.geom_road_segment_id = a.road_segment_id
                        WHERE gs.geom_3857 IS NOT NULL
                          AND gs.geom_3857 && poly.geom_3857
                          AND ST_Intersects(gs.geom_3857, poly.geom_3857)
                        ORDER BY a.point_count DESC NULLS LAST
                        """,
                        road_params,
                    )
                road_rows = cur.fetchall() or []
                features: list[dict[str, Any]] = []
                for row in road_rows:
                    geom_raw = row.get("geom_json")
                    if not geom_raw:
                        continue
                    try:
                        geom = json.loads(geom_raw)
                    except Exception:
                        continue
                    hourly_distribution = _normalize_hourly_unique_vehicles(
                        row.get("hourly_unique_vehicles_json")
                    )
                    avg_unique_vehicles_per_hour = _avg_unique_vehicles_per_hour_from_hourly(
                        hourly_distribution
                    )
                    if avg_unique_vehicles_per_hour is None and row.get("avg_unique_vehicles_per_hour") is not None:
                        try:
                            avg_fallback = float(row["avg_unique_vehicles_per_hour"])
                            if math.isfinite(avg_fallback):
                                avg_unique_vehicles_per_hour = avg_fallback
                        except Exception:
                            avg_unique_vehicles_per_hour = None
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": geom,
                            "properties": {
                                "road_segment_id": row.get("road_segment_id"),
                                "road_name": row.get("road_name"),
                                "point_count": int(row.get("point_count") or 0),
                                "avg_speed_mph": float(row["avg_speed_mph"]) if row.get("avg_speed_mph") is not None else None,
                                "speed_limit_mph": float(row["speed_limit_mph"]) if row.get("speed_limit_mph") is not None else None,
                                "min_speed_mph": float(row["min_speed_mph"]) if row.get("min_speed_mph") is not None else None,
                                "max_speed_mph": float(row["max_speed_mph"]) if row.get("max_speed_mph") is not None else None,
                                "start_ts": row.get("start_ts").isoformat() if row.get("start_ts") else None,
                                "end_ts": row.get("end_ts").isoformat() if row.get("end_ts") else None,
                                "unique_vehicles_total": int(row.get("unique_vehicles_total") or 0),
                                "avg_unique_vehicles_per_hour": avg_unique_vehicles_per_hour,
                                "hourly_unique_vehicles_json": hourly_distribution,
                            },
                        }
                    )
                area_aggregate_geojson = {"type": "FeatureCollection", "features": features}
                area_aggregate_stats = {
                    "sampled": road_segments_count > len(features),
                    "max_roads": max_roads,
                    "road_segments_total": road_segments_count,
                    "metric_scope": "segment_overlap_approx" if fast_aggregate_mode_used else "inside_polygon",
                    "fast_approximate": fast_aggregate_mode_used,
                }
                logger.info(
                    "Area analysis aggregate stage=geometry mode=%s features=%d duration_ms=%.1f",
                    "fast" if use_fast_stats_aggregate else "full",
                    len(features),
                    (time.perf_counter() - t_geometry_start) * 1000.0,
                )

                t_top_roads_start = time.perf_counter()
                if use_fast_stats_aggregate:
                    cur.execute(
                        f"""
                        SELECT
                          road_name,
                          SUM(point_count) AS count
                        FROM {area_agg_tmp_table}
                        WHERE road_name IS NOT NULL
                        GROUP BY 1
                        ORDER BY count DESC
                        LIMIT 5
                        """
                    )
                    road_counts = cur.fetchall()

                    cur.execute(
                        f"""
                        SELECT
                          road_name,
                          CASE
                            WHEN COALESCE(SUM(point_count), 0) > 0
                              THEN SUM(avg_speed_mph * point_count) / NULLIF(SUM(point_count), 0)
                            ELSE AVG(avg_speed_mph)
                          END AS avg_speed,
                          SUM(point_count) AS count
                        FROM {area_agg_tmp_table}
                        WHERE road_name IS NOT NULL
                        GROUP BY 1
                        ORDER BY count DESC
                        LIMIT 8
                        """
                    )
                    road_avg_speeds = cur.fetchall()
                else:
                    cur.execute(
                        agg_query_prefix_sql
                        + agg_cte_sql
                        + """
                        SELECT
                          road_name,
                          SUM(point_count) AS count
                        FROM agg
                        WHERE road_name IS NOT NULL
                        GROUP BY 1
                        ORDER BY count DESC
                        LIMIT 5
                        """,
                        road_params,
                    )
                    road_counts = cur.fetchall()

                    cur.execute(
                        agg_query_prefix_sql
                        + agg_cte_sql
                        + """
                        SELECT
                          road_name,
                          CASE
                            WHEN COALESCE(SUM(point_count), 0) > 0
                              THEN SUM(avg_speed_mph * point_count) / NULLIF(SUM(point_count), 0)
                            ELSE AVG(avg_speed_mph)
                          END AS avg_speed,
                          SUM(point_count) AS count
                        FROM agg
                        WHERE road_name IS NOT NULL
                        GROUP BY 1
                        ORDER BY count DESC
                        LIMIT 8
                        """,
                        road_params,
                    )
                    road_avg_speeds = cur.fetchall()
                logger.info(
                    "Area analysis aggregate stage=top_roads mode=%s duration_ms=%.1f",
                    "fast" if use_fast_stats_aggregate else "full",
                    (time.perf_counter() - t_top_roads_start) * 1000.0,
                )

            try:
                hb_table = _first_existing_relation(
                    cur,
                    _cv_relation_candidates(cur, "cv_hard_brake_events_mv")
                    + _cv_relation_candidates(cur, "cv_hard_brake"),
                )
                if not hb_table:
                    raise ValueError("hard-brake source table not found")

                hb_cols = _table_cols(hb_table)
                hb_has_dataset_col = "dataset_id" in hb_cols
                hb_has_attrs = "attrs" in hb_cols

                if "geom_m" in hb_cols:
                    hb_geom_3857 = "ST_Transform(p.geom_m, 3857)"
                elif "geom_3857" in hb_cols:
                    hb_geom_3857 = "p.geom_3857"
                elif "geom_4326" in hb_cols:
                    hb_geom_3857 = "ST_Transform(p.geom_4326, 3857)"
                elif {"lon", "lat"}.issubset(hb_cols):
                    hb_geom_3857 = "ST_Transform(ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326), 3857)"
                elif {"longitude", "latitude"}.issubset(hb_cols):
                    hb_geom_3857 = "ST_Transform(ST_SetSRID(ST_MakePoint(p.longitude, p.latitude), 4326), 3857)"
                else:
                    raise ValueError("hard-brake table has no usable geometry/coordinate columns")

                hb_params: Dict[str, Any] = {"polygon": polygon_json}
                hb_where_parts = [
                    f"{hb_geom_3857} IS NOT NULL",
                    f"ST_Intersects({hb_geom_3857}, poly.geom_3857)",
                ]
                if hb_has_dataset_col and cv_dataset_id:
                    hb_where_parts.append("p.dataset_id = %(cv_dataset_id)s")
                    hb_params["cv_dataset_id"] = cv_dataset_id
                hb_where_sql = " AND ".join(hb_where_parts)

                hb_way_terms: list[str] = []
                if "way_id" in hb_cols:
                    hb_way_terms.append("p.way_id::text")
                if "road_segment_id" in hb_cols:
                    hb_way_terms.append("p.road_segment_id::text")
                hb_way_expr = f"COALESCE({', '.join(hb_way_terms)})" if hb_way_terms else "NULL::text"
                hb_road_stats_join_sql = (
                    f"LEFT JOIN {road_stats_table} rs "
                    f"ON NULLIF(TRIM((rs.way_id)::text), '') = NULLIF(TRIM(({hb_way_expr})::text), '')"
                    if has_road_stats and hb_way_terms
                    else ""
                )
                hb_ref_terms: list[str] = []
                for col in ("ref", "route", "highway_ref", "highwayRef"):
                    if col in hb_cols:
                        hb_ref_terms.append(f"NULLIF(p.{col}::text,'')")
                hb_ref_expr = f"COALESCE({', '.join(hb_ref_terms)})" if hb_ref_terms else None
                hb_ref_attr_terms = (
                    [f"NULLIF(p.attrs->>'{key}','')" for key in _ROAD_ATTR_ROUTE_KEYS]
                    if hb_has_attrs
                    else []
                )
                hb_local_ref_terms = [term for term in [*hb_ref_terms, *hb_ref_attr_terms] if term]
                hb_local_ref_expr = (
                    f"COALESCE({', '.join(hb_local_ref_terms)})" if hb_local_ref_terms else None
                )
                hb_stats_ref_terms: list[str] = []
                if hb_road_stats_join_sql:
                    for col in ("ref", "route", "highway_ref", "highwayref"):
                        if col in road_stats_cols:
                            hb_stats_ref_terms.append(f"NULLIF(rs.{col}::text,'')")
                hb_ref_group_raw_terms = [term for term in [*hb_stats_ref_terms, hb_local_ref_expr] if term]
                hb_ref_group_raw_expr = (
                    f"COALESCE({', '.join(hb_ref_group_raw_terms)})"
                    if hb_ref_group_raw_terms
                    else None
                )
                hb_ref_group_expr = (
                    _normalized_route_ref_expr(hb_ref_group_raw_expr)
                    if hb_ref_group_raw_expr
                    else "NULL::text"
                )

                hb_name_terms: list[str] = []
                for col in ("road", "road_name", "name"):
                    if col in hb_cols:
                        hb_name_terms.append(f"NULLIF(p.{col}::text,'')")
                hb_name_expr = f"COALESCE({', '.join(hb_name_terms)})" if hb_name_terms else None
                hb_label_expr = "p.label::text" if "label" in hb_cols else None
                hb_highway_expr = "initcap(replace(p.highway::text, '_', ' '))" if "highway" in hb_cols else None
                hb_local_extra_parts = [_attrs_road_name_expr("p.attrs")] if hb_has_attrs else []
                hb_local_name_expr = _preferred_road_name_expr(
                    ref_expr=hb_ref_expr,
                    label_expr=hb_label_expr,
                    name_expr=hb_name_expr,
                    highway_expr=hb_highway_expr,
                    extra_exprs=hb_local_extra_parts,
                )
                hb_rs_name_kwargs = (
                    _road_stats_name_kwargs(road_stats_cols)
                    if hb_road_stats_join_sql
                    else {}
                )
                hb_road_name_expr = _preferred_road_name_expr(
                    **hb_rs_name_kwargs,
                    extra_exprs=[hb_local_name_expr],
                )

                hb_speed_expr = "p.speed::float8" if "speed" in hb_cols else (
                    "COALESCE("
                    "CASE WHEN NULLIF(p.attrs->>'speed','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'SpeedMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'SpeedMPH')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'speed_mph','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed_mph')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'speedMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedMPH')::float8 END"
                    ")" if hb_has_attrs else "NULL::float8"
                )
                hb_speed_limit_parts: list[str] = []
                if has_road_stats and "speed_limit_mph" in road_stats_cols:
                    hb_speed_limit_parts.append("rs.speed_limit_mph::float8")
                for col in ("speed_limit", "speed_limit_mph", "speedlimit"):
                    if col in hb_cols:
                        hb_speed_limit_parts.append(f"p.{col}::float8")
                if hb_has_attrs:
                    hb_speed_limit_parts.extend(
                        [
                            "CASE WHEN NULLIF(p.attrs->>'speed_limit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed_limit')::float8 END",
                            "CASE WHEN NULLIF(p.attrs->>'speedLimit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedLimit')::float8 END",
                            "CASE WHEN NULLIF(p.attrs->>'SpeedLimitMPH','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'SpeedLimitMPH')::float8 END",
                            "CASE WHEN NULLIF(p.attrs->>'speedlimit_mph','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedlimit_mph')::float8 END",
                        ]
                    )
                hb_speed_limit_expr = (
                    f"COALESCE({', '.join(hb_speed_limit_parts)})"
                    if hb_speed_limit_parts
                    else "NULL::float8"
                )
                hb_speed_over_limit_expr = (
                    "COALESCE("
                    "CASE WHEN NULLIF(p.attrs->>'speed_over_limit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speed_over_limit')::float8 END, "
                    "CASE WHEN NULLIF(p.attrs->>'speedOverLimit','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'speedOverLimit')::float8 END"
                    ")"
                    if hb_has_attrs
                    else f"(({hb_speed_expr}) - ({hb_speed_limit_expr}))"
                )
                hb_acc_x_expr = (
                    "p.acc_x::float8" if "acc_x" in hb_cols else (
                        "p.accx::float8" if "accx" in hb_cols else (
                            "COALESCE("
                            "CASE WHEN NULLIF(p.attrs->>'acc_x','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_x')::float8 END, "
                            "CASE WHEN NULLIF(p.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccX')::float8 END"
                            ")"
                            if hb_has_attrs
                            else "NULL::float8"
                        )
                    )
                )
                hb_acc_y_expr = (
                    "p.acc_y::float8" if "acc_y" in hb_cols else (
                        "p.accy::float8" if "accy" in hb_cols else (
                            "COALESCE("
                            "CASE WHEN NULLIF(p.attrs->>'acc_y','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'acc_y')::float8 END, "
                            "CASE WHEN NULLIF(p.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (p.attrs->>'AccY')::float8 END"
                            ")"
                            if hb_has_attrs
                            else "NULL::float8"
                        )
                    )
                )

                hb_lat_expr = (
                    "p.lat" if "lat" in hb_cols else (
                        "p.latitude" if "latitude" in hb_cols else f"ST_Y(ST_Transform({hb_geom_3857}, 4326))"
                    )
                )
                hb_lon_expr = (
                    "p.lon" if "lon" in hb_cols else (
                        "p.longitude" if "longitude" in hb_cols else f"ST_X(ST_Transform({hb_geom_3857}, 4326))"
                    )
                )
                hb_road_segment_terms: list[str] = []
                if "road_segment_id" in hb_cols:
                    hb_road_segment_terms.append("p.road_segment_id::text")
                if "way_id" in hb_cols:
                    hb_road_segment_terms.append("p.way_id::text")
                hb_road_segment_expr = (
                    f"COALESCE({', '.join(hb_road_segment_terms)})"
                    if hb_road_segment_terms
                    else "NULL::text"
                )

                if exclude_unmatched:
                    hb_where_parts.append(f"{hb_road_segment_expr} IS NOT NULL")
                    hb_where_sql = " AND ".join(hb_where_parts)

                cur.execute(
                    f"""
                    WITH poly AS (
                      SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                    )
                    SELECT COUNT(*) AS points
                    FROM {hb_table} p
                    CROSS JOIN poly
                    WHERE {hb_where_sql}
                    """,
                    hb_params,
                )
                hb_summary = cur.fetchone() or {}
                hard_brake_count = int(hb_summary.get("points") or 0)

                hb_map_point_limit: Optional[int]
                if area_mode == "detail":
                    hb_map_point_limit = map_hard_brake_point_limit
                elif fast_aggregate_mode_used:
                    hb_map_point_limit = min(
                        map_hard_brake_point_limit,
                        _AREA_ANALYSIS_FAST_AGGREGATE_HB_MAP_POINTS,
                    )
                else:
                    hb_map_point_limit = min(map_hard_brake_point_limit, _AREA_ANALYSIS_AGGREGATE_HB_MAP_POINTS)
                hb_map_point_limit_used = hb_map_point_limit

                if hb_map_point_limit is None:
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                        )
                        SELECT
                          p.ts AS timestamp,
                          {hb_lat_expr} AS latitude,
                          {hb_lon_expr} AS longitude,
                          {hb_road_segment_expr} AS road_segment_id,
                          {hb_road_name_expr} AS road_name,
                          {hb_speed_expr} AS speed,
                          {hb_speed_limit_expr} AS speed_limit,
                          {hb_speed_over_limit_expr} AS speed_over_limit,
                          {hb_acc_x_expr} AS acc_x,
                          {hb_acc_y_expr} AS acc_y
                        FROM {hb_table} p
                        {hb_road_stats_join_sql}
                        CROSS JOIN poly
                        WHERE {hb_where_sql}
                        ORDER BY p.ts NULLS LAST
                        """,
                        hb_params,
                    )
                    hard_brake_points = cur.fetchall()
                elif hb_map_point_limit > 0:
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                        )
                        SELECT
                          p.ts AS timestamp,
                          {hb_lat_expr} AS latitude,
                          {hb_lon_expr} AS longitude,
                          {hb_road_segment_expr} AS road_segment_id,
                          {hb_road_name_expr} AS road_name,
                          {hb_speed_expr} AS speed,
                          {hb_speed_limit_expr} AS speed_limit,
                          {hb_speed_over_limit_expr} AS speed_over_limit,
                          {hb_acc_x_expr} AS acc_x,
                          {hb_acc_y_expr} AS acc_y
                        FROM {hb_table} p
                        {hb_road_stats_join_sql}
                        CROSS JOIN poly
                        WHERE {hb_where_sql}
                        ORDER BY p.ts NULLS LAST
                        LIMIT %(map_hard_brake_point_limit)s
                        """,
                        {**hb_params, "map_hard_brake_point_limit": hb_map_point_limit},
                    )
                    hard_brake_points = cur.fetchall()
                else:
                    hard_brake_points = []

                if area_mode == "aggregate":
                    if hard_brake_group_by == "segment":
                        cur.execute(
                            f"""
                            WITH poly AS (
                              SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                            )
                            SELECT
                              NULLIF({hb_road_segment_expr}, '') AS road_segment_id,
                              COALESCE(NULLIF({hb_road_name_expr}, ''), '[unknown road]') AS road_name,
                              COUNT(*) AS count
                            FROM {hb_table} p
                            {hb_road_stats_join_sql}
                            CROSS JOIN poly
                            WHERE {hb_where_sql}
                              AND NULLIF({hb_road_segment_expr}, '') IS NOT NULL
                            GROUP BY 1, 2
                            ORDER BY count DESC
                            LIMIT 10
                            """,
                            hb_params,
                        )
                        hard_brake_by_road_rows = cur.fetchall() or []
                        hard_brake_by_road = []
                        for row in hard_brake_by_road_rows:
                            seg_id = str(row.get("road_segment_id") or "").strip()
                            road_name = str(row.get("road_name") or "").strip() or "[unknown road]"
                            label = f"{road_name} (#{seg_id})" if seg_id else road_name
                            hard_brake_by_road.append(
                                {
                                    "road_name": label,
                                    "count": int(row.get("count") or 0),
                                    "road_segment_id": seg_id or None,
                                }
                            )
                    elif hard_brake_group_by == "ref":
                        cur.execute(
                            f"""
                            WITH poly AS (
                              SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                            )
                            SELECT
                              COALESCE(NULLIF({hb_ref_group_expr}, ''), '[unknown ref]') AS road_ref,
                              MAX(COALESCE(NULLIF({hb_road_name_expr}, ''), '[unknown road]')) AS road_name_sample,
                              COUNT(*) AS count
                            FROM {hb_table} p
                            {hb_road_stats_join_sql}
                            CROSS JOIN poly
                            WHERE {hb_where_sql}
                            GROUP BY 1
                            ORDER BY count DESC
                            LIMIT 10
                            """,
                            hb_params,
                        )
                        hard_brake_by_road_rows = cur.fetchall() or []
                        hard_brake_by_road = []
                        for row in hard_brake_by_road_rows:
                            road_ref = str(row.get("road_ref") or "").strip()
                            road_name_sample = str(row.get("road_name_sample") or "").strip()
                            if road_ref and road_ref != "[unknown ref]":
                                label = f"{road_ref} | {road_name_sample}" if road_name_sample else road_ref
                            else:
                                label = road_name_sample or "[unknown road]"
                            hard_brake_by_road.append(
                                {
                                    "road_name": label,
                                    "count": int(row.get("count") or 0),
                                    "road_ref": road_ref or None,
                                }
                            )
                    else:
                        cur.execute(
                            f"""
                            WITH poly AS (
                              SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                            )
                            SELECT
                              COALESCE(NULLIF({hb_road_name_expr}, ''), '[unknown road]') AS road_name,
                              COUNT(*) AS count
                            FROM {hb_table} p
                            {hb_road_stats_join_sql}
                            CROSS JOIN poly
                            WHERE {hb_where_sql}
                            GROUP BY 1
                            ORDER BY count DESC
                            LIMIT 10
                            """,
                            hb_params,
                        )
                        hard_brake_by_road = cur.fetchall() or []
                    if hb_road_segment_expr != "NULL::text":
                        cur.execute(
                            f"""
                            WITH poly AS (
                              SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 3857) AS geom_3857
                            )
                            SELECT
                              NULLIF({hb_road_segment_expr}, '') AS road_segment_id,
                              COUNT(*) AS count
                            FROM {hb_table} p
                            {hb_road_stats_join_sql}
                            CROSS JOIN poly
                            WHERE {hb_where_sql}
                              AND NULLIF({hb_road_segment_expr}, '') IS NOT NULL
                            GROUP BY 1
                            """,
                            hb_params,
                        )
                        hard_brake_by_segment = {
                            str(row.get("road_segment_id")).strip(): int(row.get("count") or 0)
                            for row in (cur.fetchall() or [])
                            if str(row.get("road_segment_id") or "").strip()
                        }
            except Exception as hb_err:
                logger.warning("Area analysis hard-brake query failed: %s", hb_err)
                hard_brake_available = False
                hard_brake_count = 0
                hard_brake_points = []
                hard_brake_by_road = []
                hard_brake_by_segment = {}

            if crash_dataset_id:
                crash_params = {
                    "polygon": polygon_json,
                    "crash_dataset_id": crash_dataset_id,
                }
                if cv_dataset_id:
                    crash_params["cv_dataset_id"] = cv_dataset_id
                if area_mode == "detail":
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                        )
                        SELECT
                          e.id,
                          e.lat AS latitude,
                          e.lon AS longitude,
                          e.ts AS timestamp,
                          e.road_segment_id,
                          COALESCE(NULLIF(e.props->>'road_name',''), NULLIF(e.road_segment_id::text,'')) AS road_name,
                          COALESCE(NULLIF(e.props->>'severity',''), NULLIF(e.props->>'crashSeverity','')) AS severity,
                          NULLIF(e.props->>'accident_date','') AS accident_date,
                          NULLIF(e.props->>'accident_time','') AS accident_time
                        FROM {APP_EVENTS} e
                        CROSS JOIN poly
                        WHERE e.dataset_id = %(crash_dataset_id)s
                          AND e.geom_m IS NOT NULL
                          AND e.lat IS NOT NULL
                          AND e.lon IS NOT NULL
                          AND ST_Intersects(e.geom_m, poly.geom_m)
                        """,
                        crash_params,
                    )
                    crash_points = cur.fetchall()
                    crash_total_count = len(crash_points)
                else:
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                        )
                        SELECT COUNT(*) AS count
                        FROM {APP_EVENTS} e
                        CROSS JOIN poly
                        WHERE e.dataset_id = %(crash_dataset_id)s
                          AND e.geom_m IS NOT NULL
                          AND e.lat IS NOT NULL
                          AND e.lon IS NOT NULL
                          AND ST_Intersects(e.geom_m, poly.geom_m)
                        """,
                        crash_params,
                    )
                    crash_total_row = cur.fetchone() or {}
                    crash_total_count = int(crash_total_row.get("count") or 0)
                    crash_map_point_limit = max(0, _AREA_ANALYSIS_AGGREGATE_CRASH_MAP_POINTS)
                    if crash_map_point_limit > 0:
                        cur.execute(
                            f"""
                            WITH poly AS (
                              SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                            )
                            SELECT
                              e.id,
                              e.lat AS latitude,
                              e.lon AS longitude,
                              e.ts AS timestamp,
                              e.road_segment_id,
                              COALESCE(NULLIF(e.props->>'road_name',''), NULLIF(e.road_segment_id::text,'')) AS road_name,
                              COALESCE(NULLIF(e.props->>'severity',''), NULLIF(e.props->>'crashSeverity','')) AS severity,
                              NULLIF(e.props->>'accident_date','') AS accident_date,
                              NULLIF(e.props->>'accident_time','') AS accident_time
                            FROM {APP_EVENTS} e
                            CROSS JOIN poly
                            WHERE e.dataset_id = %(crash_dataset_id)s
                              AND e.geom_m IS NOT NULL
                              AND e.lat IS NOT NULL
                              AND e.lon IS NOT NULL
                              AND ST_Intersects(e.geom_m, poly.geom_m)
                            ORDER BY e.ts NULLS LAST
                            LIMIT %(crash_map_point_limit)s
                            """,
                            {**crash_params, "crash_map_point_limit": crash_map_point_limit},
                        )
                        crash_points = cur.fetchall()
                    else:
                        crash_points = []

                cur.execute(
                    f"""
                    WITH poly AS (
                      SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                    )
                    SELECT
                      COALESCE(NULLIF(e.props->>'severity',''), NULLIF(e.props->>'crashSeverity',''), 'UNKNOWN') AS severity,
                      COUNT(*) AS count
                    FROM {APP_EVENTS} e
                    CROSS JOIN poly
                    WHERE e.dataset_id = %(crash_dataset_id)s
                      AND e.geom_m IS NOT NULL
                      AND e.lat IS NOT NULL
                      AND e.lon IS NOT NULL
                      AND ST_Intersects(e.geom_m, poly.geom_m)
                    GROUP BY 1
                    ORDER BY count DESC
                    """,
                    crash_params,
                )
                for row in cur.fetchall():
                    crash_counts[str(row.get("severity") or "UNKNOWN")] = int(row.get("count") or 0)

                if area_mode == "aggregate":
                    crash_rs_join_sql = ""
                    if has_road_stats:
                        crash_rs_where = (
                            " AND rs_crash.dataset_id = %(cv_dataset_id)s"
                            if "dataset_id" in road_stats_cols and cv_dataset_id
                            else ""
                        )
                        crash_rs_join_sql = (
                            f"LEFT JOIN {road_stats_table} rs_crash "
                            f"ON rs_crash.way_id::text = e.road_segment_id::text{crash_rs_where}"
                        )
                    crash_ref_terms = [f"NULLIF(rs_crash.{c}::text,'')" for c in ("ref", "route", "highway_ref", "highwayRef") if c in road_stats_cols]
                    crash_ref_expr = f"COALESCE({', '.join(crash_ref_terms)})" if crash_ref_terms else None
                    crash_name_terms = [f"NULLIF(rs_crash.{c}::text,'')" for c in ("label", "name", "road_name") if c in road_stats_cols]
                    crash_name_expr = f"COALESCE({', '.join(crash_name_terms)})" if crash_name_terms else None
                    crash_highway_expr = (
                        "initcap(replace(rs_crash.highway::text, '_', ' '))"
                        if "highway" in road_stats_cols
                        else None
                    )
                    crash_road_name_expr = _preferred_road_name_expr(
                        ref_expr=crash_ref_expr,
                        name_expr=crash_name_expr,
                        highway_expr=crash_highway_expr,
                        extra_exprs=[
                            "NULLIF(e.props->>'road_name','')",
                            "NULLIF(e.road_segment_id::text,'')",
                        ],
                    )
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                        )
                        SELECT
                          COALESCE(NULLIF({crash_road_name_expr}, ''), '[unknown road]') AS road_name,
                          COUNT(*) AS count
                        FROM {APP_EVENTS} e
                        {crash_rs_join_sql}
                        CROSS JOIN poly
                        WHERE e.dataset_id = %(crash_dataset_id)s
                          AND e.geom_m IS NOT NULL
                          AND e.lat IS NOT NULL
                          AND e.lon IS NOT NULL
                          AND ST_Intersects(e.geom_m, poly.geom_m)
                        GROUP BY 1
                        ORDER BY count DESC
                        LIMIT 10
                        """,
                        crash_params,
                    )
                    crash_by_road = cur.fetchall() or []
                    cur.execute(
                        f"""
                        WITH poly AS (
                          SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                        )
                        SELECT
                          NULLIF(e.road_segment_id::text, '') AS road_segment_id,
                          COUNT(*) AS count
                        FROM {APP_EVENTS} e
                        CROSS JOIN poly
                        WHERE e.dataset_id = %(crash_dataset_id)s
                          AND e.geom_m IS NOT NULL
                          AND e.lat IS NOT NULL
                          AND e.lon IS NOT NULL
                          AND ST_Intersects(e.geom_m, poly.geom_m)
                          AND NULLIF(e.road_segment_id::text, '') IS NOT NULL
                        GROUP BY 1
                        """,
                        crash_params,
                    )
                    crash_by_segment = {
                        str(row.get("road_segment_id")).strip(): int(row.get("count") or 0)
                        for row in (cur.fetchall() or [])
                        if str(row.get("road_segment_id") or "").strip()
                    }

            if workzone_dataset_id:
                cur.execute(
                    f"""
                    WITH poly AS (
                      SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(polygon)s), 4326), 26915) AS geom_m
                    )
                    SELECT
                      e.id,
                      e.road_segment_id,
                      e.lat,
                      e.lon,
                      e.props->>'start_date' AS start_date,
                      e.props->>'end_date' AS end_date,
                      e.props->>'geometry' AS geometry,
                      e.props->>'core_details' AS core_details,
                      e.props->>'vehicle_impact' AS vehicle_impact
                    FROM {APP_EVENTS} e
                    CROSS JOIN poly
                    WHERE e.dataset_id = %(workzone_dataset_id)s
                      AND e.geom_m IS NOT NULL
                      AND ST_Intersects(e.geom_m, poly.geom_m)
                    """,
                    {
                        "polygon": polygon_json,
                        "workzone_dataset_id": workzone_dataset_id,
                    },
                )
                wz_rows = cur.fetchall()
                if wz_rows:
                    workzone_lines = _make_workzone_map_payload(
                        wz_rows,
                        label="Workzones in area",
                        exclusive=True,
                        dataset_id=workzone_dataset_id,
                    ).get("lines", [])

        points = int(summary.get("points") or 0)
        vehicles = int(summary.get("vehicles") or 0)
        normalized_hourly_unique_vehicles = _normalize_hourly_unique_vehicles(
            summary.get("hourly_unique_vehicles")
        )
        avg_unique_vehicles_per_hour = _avg_unique_vehicles_per_hour_from_hourly(
            normalized_hourly_unique_vehicles
        )
        avg_speed = summary.get("avg_speed")
        min_speed = summary.get("min_speed")
        max_speed = summary.get("max_speed")
        min_ts = summary.get("min_ts")
        max_ts = summary.get("max_ts")
        limit_points = int(summary.get("limit_points") or 0)
        speeding_points = int(summary.get("speeding_points") or 0)
        under_points = int(summary.get("under_points") or 0)
        speeding_pct = (speeding_points / limit_points * 100.0) if limit_points else 0.0
        under_pct = (under_points / limit_points * 100.0) if limit_points else 0.0

        crash_count = crash_total_count if area_mode == "aggregate" else len(crash_points)
        crash_unique_locations = 0
        if area_mode == "detail" and crash_points:
            try:
                crash_unique_locations = len({(p.get("latitude"), p.get("longitude")) for p in crash_points})
            except Exception:
                crash_unique_locations = 0

        cv_points_line = (
            f"- CV points: {points:,} across {vehicles:,} vehicles"
            if vehicles > 0
            else f"- CV points: {points:,} (vehicle IDs unavailable in this dataset)"
        )
        response_lines = [
            f"Area analysis (polygon, mode={area_mode}):",
            cv_points_line,
            f"- Area size: {area_km2:,.1f} km^2",
            f"- Unmatched CV/hard-brake points excluded: {'yes' if exclude_unmatched else 'no'}",
            f"- Avg speed: {avg_speed:.1f} mph" if avg_speed is not None else "- Avg speed: N/A",
            f"- Min / Max speed: {min_speed:.1f} / {max_speed:.1f} mph" if min_speed is not None and max_speed is not None else "- Min / Max speed: N/A",
        ]
        if limit_points > 0:
            response_lines.append(
                f"- Speeding (>10 mph over limit): {speeding_points:,} ({speeding_pct:.1f}% of limit-known points)"
            )
            response_lines.append(
                f"- Under limit (>10 mph below): {under_points:,} ({under_pct:.1f}% of limit-known points)"
            )
        else:
            response_lines.append("- Speed compliance breakdown: no limit-known points in this polygon")
        if area_mode == "aggregate":
            response_lines.append(f"- Aggregated roads in polygon: {road_segments_count:,}")
            if fast_aggregate_mode_used:
                response_lines.append(
                    "- Road metrics are summarized from polygon-overlap segments for this large area."
                )
        if min_ts and max_ts:
            response_lines.append(f"- Time range: {min_ts} → {max_ts}")
        if area_mode == "detail" and points > len(cv_points):
            response_lines.append(
                f"- Map payload sampled: showing {len(cv_points):,} of {points:,} CV points"
            )
        if area_mode == "aggregate" and area_aggregate_stats.get("sampled"):
            response_lines.append(
                f"- Road geometry sampled: showing {len(area_aggregate_geojson.get('features', [])):,} of {road_segments_count:,} roads"
            )
        if hard_brake_available and hb_map_point_limit_used is not None and hard_brake_count > len(hard_brake_points):
            response_lines.append(
                f"- Hard-brake map payload sampled: showing {len(hard_brake_points):,} of {hard_brake_count:,} points"
            )

        if not crash_data_available:
            response_lines.append("- Crashes in area: no data uploaded")
        elif crash_count > 0:
            if crash_unique_locations and crash_unique_locations != crash_count:
                response_lines.append(
                    f"- Crashes in area: {crash_count} ({crash_unique_locations} unique locations)"
                )
            else:
                response_lines.append(f"- Crashes in area: {crash_count}")
        else:
            response_lines.append("- Crashes in area: 0")

        if not workzone_data_available:
            response_lines.append("- Workzones in area: no data uploaded")
        elif workzone_lines:
            response_lines.append(f"- Workzones in area: {len(workzone_lines)}")
        else:
            response_lines.append("- Workzones in area: 0")

        if not hard_brake_available:
            response_lines.append("- Hard braking in area: no data available")
        else:
            response_lines.append(f"- Hard braking in area: {hard_brake_count}")
            if area_mode == "aggregate":
                hb_group_label = {
                    "segment": "segment",
                    "road_name": "road name",
                    "ref": "route ref",
                }.get(hard_brake_group_by, hard_brake_group_by)
                response_lines.append(f"- Hard-brake ranking grouped by: {hb_group_label}")

        if road_counts:
            top_roads = ", ".join([f"{r.get('road_name')} ({r.get('count')})" for r in road_counts if r.get("road_name")])
            if top_roads:
                response_lines.append(f"- Top roads by points: {top_roads}")

        if crash_counts:
            breakdown = ", ".join([f"{k}: {v}" for k, v in crash_counts.items()])
            if breakdown:
                response_lines.append(f"- Crash severity breakdown: {breakdown}")
        if crash_by_road:
            top_crash_roads = ", ".join([f"{r.get('road_name')} ({r.get('count')})" for r in crash_by_road if r.get("road_name")])
            if top_crash_roads:
                response_lines.append(f"- Top crash roads: {top_crash_roads}")
        if hard_brake_by_road:
            top_hb_roads = ", ".join([f"{r.get('road_name')} ({r.get('count')})" for r in hard_brake_by_road if r.get("road_name")])
            if top_hb_roads:
                hb_group_label = {
                    "segment": "segment",
                    "road_name": "road name",
                    "ref": "route ref",
                }.get(hard_brake_group_by, hard_brake_group_by)
                response_lines.append(f"- Top hard-brake roads (grouped by {hb_group_label}): {top_hb_roads}")

        if area_mode == "aggregate" and area_aggregate_geojson.get("features"):
            crash_by_road_lookup = {
                str(row.get("road_name") or "").strip().lower(): int(row.get("count") or 0)
                for row in crash_by_road
                if str(row.get("road_name") or "").strip()
            }
            hard_brake_by_road_lookup = {
                str(row.get("road_name") or "").strip().lower(): int(row.get("count") or 0)
                for row in hard_brake_by_road
                if str(row.get("road_name") or "").strip()
            }
            for feature in area_aggregate_geojson.get("features", []):
                props = feature.get("properties") or {}
                segment_key = str(props.get("road_segment_id") or "").strip()
                road_key = str(props.get("road_name") or "").strip().lower()
                crash_count_value = (
                    crash_by_segment.get(segment_key, 0)
                    if segment_key
                    else crash_by_road_lookup.get(road_key, 0)
                )
                hard_brake_count_value = (
                    hard_brake_by_segment.get(segment_key, 0)
                    if segment_key
                    else hard_brake_by_road_lookup.get(road_key, 0)
                )
                props["crash_count"] = int(crash_count_value or 0)
                props["hard_brake_count"] = int(hard_brake_count_value or 0)
                props["area_analysis"] = True
                feature["properties"] = props

        map_points: list[dict[str, Any]] = []
        if area_mode == "detail":
            for p in cv_points:
                speed = p.get("speed")
                speed_limit_mph = p.get("speed_limit_mph")
                if speed_limit_mph is None:
                    speed_limit_mph = p.get("speedlimit_mph")
                if speed is not None:
                    speed = float(speed)
                if speed_limit_mph is not None:
                    speed_limit_mph = float(speed_limit_mph)
                map_points.append({
                    "latitude": p.get("latitude"),
                    "longitude": p.get("longitude"),
                    "timestamp": p.get("timestamp"),
                    "road_segment_id": p.get("road_segment_id"),
                    "road_name": p.get("road_name"),
                    "speed": speed,
                    "SpeedLimitMPH": speed_limit_mph,
                    "speed_over_limit": (
                        (speed - speed_limit_mph)
                        if speed is not None and speed_limit_mph is not None
                        else None
                    ),
                    "acceleration": {
                        "x": p.get("acc_x"),
                        "y": p.get("acc_y"),
                    },
                    "type": "Vehicle",
                    "point_type": "Traffic",
                })
        map_points += [
            {
                "latitude": p.get("latitude"),
                "longitude": p.get("longitude"),
                "timestamp": p.get("timestamp"),
                "road_segment_id": p.get("road_segment_id"),
                "road_name": p.get("road_name"),
                "severity": p.get("severity"),
                "accident_date": p.get("accident_date"),
                "accident_time": p.get("accident_time"),
                "type": "Crash",
                "point_type": "Crash",
            }
            for p in crash_points
        ]
        map_points += [
            {
                "latitude": p.get("latitude"),
                "longitude": p.get("longitude"),
                "timestamp": p.get("timestamp"),
                "road_segment_id": p.get("road_segment_id"),
                "road_name": p.get("road_name"),
                "speed": p.get("speed"),
                "SpeedLimitMPH": p.get("speed_limit"),
                "speed_over_limit": p.get("speed_over_limit"),
                "acceleration": {
                    "x": p.get("acc_x"),
                    "y": p.get("acc_y"),
                },
                "decelerationG": p.get("acc_x"),
                "type": "HardBrake",
                "point_type": "HardBrake",
            }
            for p in hard_brake_points
        ]

        result_payload = {
            "status": "success",
            "mode": area_mode,
            "response": "\n".join(response_lines),
            "summary": {
                "points": points,
                "map_points_returned": len(cv_points),
                "map_points_limit": map_cv_point_limit,
                "vehicles": vehicles,
                "avg_speed": avg_speed,
                "min_speed": min_speed,
                "max_speed": max_speed,
                "avg_unique_vehicles_per_hour": avg_unique_vehicles_per_hour,
                "hourly_unique_vehicles": normalized_hourly_unique_vehicles,
                "speeding_points": speeding_points,
                "under_points": under_points,
                "limit_points": limit_points,
                "speeding_pct": speeding_pct,
                "under_pct": under_pct,
                "time_start": str(min_ts) if min_ts else None,
                "time_end": str(max_ts) if max_ts else None,
                "area_km2": area_km2,
                "road_segments": road_segments_count,
                "crashes": crash_count,
                "crashes_unique_locations": crash_unique_locations,
                "workzones": len(workzone_lines),
                "hard_brakes": hard_brake_count,
                "hard_brake_data_available": hard_brake_available,
                "hard_brake_group_by": hard_brake_group_by,
                "exclude_unmatched": exclude_unmatched,
                "crash_data_available": crash_data_available,
                "workzone_data_available": workzone_data_available,
                "speed_compliance_available": limit_points > 0,
                "vehicles_available": vehicles > 0,
                "use_hard_brake_secondary": fast_aggregate_mode_used,
                "secondary_stat_label": "Hard Brake Events" if fast_aggregate_mode_used else ("Vehicles" if vehicles > 0 else "CV Points"),
                "secondary_stat_value": hard_brake_count if fast_aggregate_mode_used else (vehicles if vehicles > 0 else points),
                "aggregation_mode": area_mode,
                "top_roads": road_counts,
                "crash_breakdown": crash_counts,
                "crash_by_road": crash_by_road,
                "hard_brake_by_road": hard_brake_by_road,
                "road_avg_speeds": road_avg_speeds,
                "fast_aggregate_mode": fast_aggregate_mode_used,
                "approximate": fast_aggregate_mode_used,
            },
            "mapSelection": {
                "label": "Polygon area analysis",
                "count": len(map_points),
                "points": map_points,
                "lines": workzone_lines,
                "overlay": False,
            },
            "areaAggregate": {
                "label": "Polygon road aggregates",
                "count": len(area_aggregate_geojson.get("features", [])),
                "geojson": area_aggregate_geojson,
                "render": {
                    "layer_mode": "road-network" if area_mode == "aggregate" else "focus-selection",
                    "show_points": area_mode == "detail",
                },
                "stats": area_aggregate_stats,
            },
        }
        _persist_analysis_context(
            _build_area_analysis_context(
                response_text=result_payload.get("response", ""),
                summary=result_payload.get("summary", {}) if isinstance(result_payload.get("summary"), dict) else {},
                mode=area_mode,
                polygon_json=polygon_json,
                cv_dataset_id=cv_dataset_id,
                crash_dataset_id=crash_dataset_id,
                workzone_dataset_id=workzone_dataset_id,
            ),
            session_id=session_id,
        )
        if area_mode == "aggregate":
            _area_analysis_cache_put(area_cache_key, result_payload)
        return result_payload
    except Exception as e:
        err_text = str(e or "")
        if isinstance(e, psycopg2.errors.QueryCanceled) or "statement timeout" in err_text.lower():
            logger.warning("Area analysis timed out for selected polygon: %s", err_text)
            raise HTTPException(
                status_code=408,
                detail=(
                    "Area analysis took too long for this selection. "
                    "Try a smaller polygon and run the analysis again."
                ),
            )
        logger.error(f"Area analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=err_text)

@router.post("/api/workzone/analyze")
def analyze_workzone(
    payload: WorkzoneAnalyzeRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    session_id = _require_session(x_session_id)
    user_id = (get_active_user() or "dev-user").strip() or "dev-user"
    try:
        if not payload.workzone_id:
            raise ValueError("workzone_id is required.")
        workzone_row_id = _normalize_workzone_row_id(payload.workzone_id)
        if workzone_row_id is None:
            raise ValueError("Invalid workzone_id. Expected an event id.")

        workzone_dataset_id = payload.dataset_id or _latest_workzone_dataset_id(session_id)
        if not workzone_dataset_id:
            raise ValueError("No workzone dataset_id found for this session.")

        cv_dataset_id = payload.cv_dataset_id or _latest_cv_dataset_id()
        crash_dataset_id = _latest_event_dataset_id(session_id, "crash")

        distance_m = float(payload.distance_m or 200.0)
        road_match_m = min(distance_m, 75.0)
        crash_points: list[dict] = []
        crash_points_total = 0

        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Prevent long-running spatial scans from leaving the UI spinning.
            cur.execute(f"SET LOCAL statement_timeout = '{_WORKZONE_ANALYSIS_STMT_TIMEOUT_MS}ms'")
            cv_ctx = _resolve_cv_analysis_context(cur)
            hb_ctx = _resolve_hard_brake_context(cur)
            cv_dataset_clause = "AND p.dataset_id = %(cv_dataset_id)s" if cv_ctx["has_cv_dataset_col"] and cv_dataset_id else ""
            if payload.cv_dataset_id and not cv_ctx["has_cv_dataset_col"]:
                logger.info("Workzone analysis: ignoring cv_dataset_id filter because cv_points has no dataset_id column")

                cur.execute(
                    f"""
                SELECT
                  e.id,
                  e.road_segment_id,
                  e.lat,
                  e.lon,
                  e.props->>'start_date' AS start_date,
                  e.props->>'end_date' AS end_date,
                  e.props->>'geometry' AS geometry,
                  e.props->>'core_details' AS core_details
                FROM {APP_EVENTS} e
                WHERE e.dataset_id=%s AND e.owner_user_id=%s AND e.id=%s
                """,
                (workzone_dataset_id, user_id, workzone_row_id),
            )
            wz_row = cur.fetchone()

            if not wz_row:
                raise ValueError("Workzone not found for this user.")

            road_segment_id = payload.road_segment_id or wz_row.get("road_segment_id")
            start_ts = payload.start_date or wz_row.get("start_date")
            end_ts = payload.end_date or wz_row.get("end_date")

            if isinstance(road_segment_id, str) and road_segment_id.strip().lower() in {"", "0", "null", "none"}:
                road_segment_id = None
            if isinstance(start_ts, str) and not start_ts.strip():
                start_ts = None
            if isinstance(end_ts, str) and not end_ts.strip():
                end_ts = None

            if not start_ts or not end_ts:
                return {
                    "status": "success",
                    "response": "Workzone analysis skipped: workzone is missing start/end dates.",
                    "mapSelection": None,
                    "chartPayload": [],
                }

            geom_json = _parse_geojson_text(wz_row.get("geometry"))

            if not road_segment_id:
                if cv_ctx["has_road_stats"] and cv_ctx["road_stats_geom_m_expr"]:
                    cur.execute(
                        f"""
                        WITH wz AS (
                          SELECT
                            CASE
                              WHEN %(geom_json)s IS NOT NULL
                                THEN ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geom_json)s), 4326), 26915)
                              ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%(wz_lon)s, %(wz_lat)s), 4326), 26915)
                            END AS wz_geom_m
                        )
                        SELECT r.road_segment_id, r.road_name
                        FROM wz
                        JOIN LATERAL (
                          SELECT
                            rs.way_id::text AS road_segment_id,
                            {_preferred_road_name_expr(**(cv_ctx.get("road_stats_name_kwargs") or {}))} AS road_name
                          FROM {cv_ctx["road_stats_table"]} rs
                          WHERE {cv_ctx["road_stats_geom_m_expr"]} IS NOT NULL
                            AND ST_DWithin({cv_ctx["road_stats_geom_m_expr"]}, wz.wz_geom_m, %(road_match_m)s)
                          ORDER BY {cv_ctx["road_stats_geom_m_expr"]} <-> wz.wz_geom_m
                          LIMIT 1
                        ) r ON TRUE
                        """,
                        {
                            "geom_json": geom_json,
                            "wz_lon": wz_row.get("lon"),
                            "wz_lat": wz_row.get("lat"),
                            "road_match_m": road_match_m,
                        },
                    )
                    road_match = cur.fetchone()
                    if road_match:
                        road_segment_id = road_match.get("road_segment_id")
                        wz_row["road_name"] = road_match.get("road_name")

            if not road_segment_id:
                return {
                    "status": "success",
                    "response": "Workzone analysis skipped: no road segment match found.",
                    "mapSelection": None,
                    "chartPayload": [],
                }

            if "road_name" not in wz_row:
                if cv_ctx["has_road_stats"]:
                    cur.execute(
                        f"""
                        SELECT {_preferred_road_name_expr(**(cv_ctx.get("road_stats_name_kwargs") or {}))} AS road_name
                        FROM {cv_ctx["road_stats_table"]} rs
                        WHERE NULLIF(TRIM((rs.way_id)::text), '') = NULLIF(TRIM((%s)::text), '')
                        LIMIT 1
                        """,
                        (road_segment_id,),
                    )
                    road_name_row = cur.fetchone()
                    if road_name_row:
                        wz_row["road_name"] = road_name_row.get("road_name") if isinstance(road_name_row, dict) else road_name_row[0]

            params = {
                "cv_dataset_id": cv_dataset_id,
                "road_segment_id": road_segment_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "distance_m": distance_m,
                "geom_json": geom_json,
                "wz_lon": wz_row.get("lon"),
                "wz_lat": wz_row.get("lat"),
                "map_point_limit": _WORKZONE_ANALYSIS_MAX_MAP_POINTS,
            }
            cv_cols = set(cv_ctx.get("cv_cols") or set())
            has_attrs = bool(cv_ctx.get("has_attrs"))
            has_native_road_key = ("road_segment_id" in cv_cols) or ("way_id" in cv_cols)
            speed_expr_fast = (
                "p.speed::float8" if "speed" in cv_cols else (
                    "COALESCE("
                    "NULLIF(p.attrs->>'speed','')::float8, "
                    "NULLIF(p.attrs->>'SpeedMPH','')::float8, "
                    "NULLIF(p.attrs->>'speed_mph','')::float8, "
                    "NULLIF(p.attrs->>'speedMPH','')::float8"
                    ")" if has_attrs else "NULL::float8"
                )
            )
            speed_limit_parts_fast: list[str] = []
            if "speed_limit_mph" in cv_cols:
                speed_limit_parts_fast.append(
                    "CASE WHEN NULLIF(p.speed_limit_mph::text,'') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN p.speed_limit_mph::float8 END"
                )
            if has_attrs:
                speed_limit_parts_fast.extend(
                    [
                        "NULLIF(p.attrs->>'speed_limit_mph','')::float8",
                        "NULLIF(p.attrs->>'speed_limit','')::float8",
                        "NULLIF(p.attrs->>'speedlimit_mph','')::float8",
                        "NULLIF(p.attrs->>'SpeedLimitMPH','')::float8",
                        "NULLIF(p.attrs->>'speedLimit','')::float8",
                        "NULLIF(p.attrs->>'SpeedLimit','')::float8",
                    ]
                )
            speed_limit_expr_fast = (
                f"COALESCE({', '.join(speed_limit_parts_fast)})"
                if speed_limit_parts_fast
                else "NULL::float8"
            )
            road_name_parts_fast: list[str] = []
            if has_attrs:
                road_name_parts_fast.append(_attrs_road_name_expr("p.attrs"))
            if "road_name" in cv_cols:
                road_name_parts_fast.append("NULLIF(p.road_name::text,'')")
            if "name" in cv_cols:
                road_name_parts_fast.append("NULLIF(p.name::text,'')")
            road_name_expr_fast = (
                f"COALESCE({', '.join(road_name_parts_fast)})"
                if road_name_parts_fast
                else "NULL::text"
            )
            road_segment_filter_expr = (
                "p.road_segment_id::text"
                if "road_segment_id" in cv_cols
                else ("p.way_id::text" if "way_id" in cv_cols else cv_ctx["road_segment_expr"])
            )
            from_sql_fast = f"FROM {cv_ctx['cv_table']} p" if has_native_road_key else cv_ctx["from_sql"]
            lat_expr_fast = cv_ctx["lat_expr"]
            lon_expr_fast = cv_ctx["lon_expr"]
            if not has_native_road_key:
                # Fallback: use full resolved expressions when the native road key is not present.
                speed_expr_fast = cv_ctx["speed_expr"]
                speed_limit_expr_fast = cv_ctx["speed_limit_expr"]
                road_name_expr_fast = cv_ctx["road_name_expr"]

            cur.execute(
                f"""
                WITH wz AS (
                  SELECT
                    %(road_segment_id)s::text AS road_segment_id,
                    CASE
                      WHEN %(geom_json)s IS NOT NULL
                        THEN ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geom_json)s), 4326), 26915)
                      ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%(wz_lon)s, %(wz_lat)s), 4326), 26915)
                    END AS wz_geom_m,
                    %(start_ts)s::timestamptz AS start_ts,
                    %(end_ts)s::timestamptz AS end_ts
                ),
                wz_window AS (
                  SELECT
                    road_segment_id,
                    wz_geom_m,
                    start_ts AS during_start,
                    end_ts AS during_end
                  FROM wz
                ),
                road_time_candidates AS MATERIALIZED (
                  SELECT
                    p.ts AS timestamp,
                    {lat_expr_fast} AS latitude,
                    {lon_expr_fast} AS longitude,
                    {road_segment_filter_expr} AS road_segment_id,
                    {road_name_expr_fast} AS road_name,
                    {speed_expr_fast} AS speed,
                    {speed_limit_expr_fast} AS speed_limit,
                    ({speed_expr_fast} - {speed_limit_expr_fast}) AS speed_over_limit,
                    {cv_ctx["vehicle_id_expr"]} AS vehicle_id,
                    {cv_ctx["geom_m_expr"]} AS geom_m
                  {from_sql_fast}
                  CROSS JOIN wz_window wz
                  WHERE p.ts IS NOT NULL
                    {cv_dataset_clause}
                    AND wz.during_start IS NOT NULL
                    AND wz.during_end IS NOT NULL
                    AND {road_segment_filter_expr} = wz.road_segment_id
                    AND p.ts BETWEEN wz.during_start AND wz.during_end
                ),
                spatial_candidates AS MATERIALIZED (
                  SELECT
                    c.timestamp,
                    c.latitude,
                    c.longitude,
                    c.road_segment_id,
                    c.road_name,
                    c.speed,
                    c.speed_limit,
                    c.speed_over_limit,
                    c.vehicle_id
                  FROM road_time_candidates c
                  CROSS JOIN wz_window wz
                  WHERE c.geom_m IS NOT NULL
                    AND c.geom_m && ST_Expand(wz.wz_geom_m, %(distance_m)s)
                    AND ST_DWithin(c.geom_m, wz.wz_geom_m, %(distance_m)s)
                ),
                summary AS (
                  SELECT
                    (SELECT COUNT(*) FROM road_time_candidates) AS with_points,
                    (SELECT COUNT(DISTINCT vehicle_id) FROM road_time_candidates) AS with_vehicles,
                    (SELECT AVG(speed) FROM road_time_candidates) AS with_avg_speed,
                    (SELECT COUNT(*) FROM spatial_candidates) AS spatial_points,
                    COUNT(*) AS conflated_points,
                    MIN(wz.during_start) AS during_start,
                    MAX(wz.during_end) AS during_end
                  FROM spatial_candidates c
                  CROSS JOIN wz_window wz
                ),
                spatial_points AS (
                  SELECT
                    c.latitude,
                    c.longitude,
                    c.timestamp,
                    c.road_segment_id,
                    c.road_name,
                    c.speed,
                    c.speed_limit AS "speedLimit",
                    c.speed_over_limit
                  FROM spatial_candidates c
                  ORDER BY c.timestamp NULLS LAST
                  LIMIT %(map_point_limit)s
                ),
                road_time_points AS (
                  SELECT
                    c.latitude,
                    c.longitude,
                    c.timestamp,
                    c.road_segment_id,
                    c.road_name,
                    c.speed,
                    c.speed_limit AS "speedLimit",
                    c.speed_over_limit
                  FROM road_time_candidates c
                  ORDER BY c.timestamp NULLS LAST
                  LIMIT %(map_point_limit)s
                )
                SELECT
                  s.with_points,
                  s.with_vehicles,
                  s.with_avg_speed,
                  s.spatial_points,
                  s.conflated_points,
                  s.during_start,
                  s.during_end,
                  COALESCE(
                    (SELECT jsonb_agg(to_jsonb(p)) FROM spatial_points p),
                    '[]'::jsonb
                  ) AS spatial_points_sample,
                  COALESCE(
                    (SELECT jsonb_agg(to_jsonb(p)) FROM road_time_points p),
                    '[]'::jsonb
                  ) AS road_time_points_sample
                FROM summary s
                """,
                params,
            )
            summary = cur.fetchone() or {}

            spatial_points_sample = summary.get("spatial_points_sample") or []
            if isinstance(spatial_points_sample, str):
                try:
                    spatial_points_sample = json.loads(spatial_points_sample)
                except Exception:
                    spatial_points_sample = []
            if not isinstance(spatial_points_sample, list):
                spatial_points_sample = []

            road_time_points_sample = summary.get("road_time_points_sample") or []
            if isinstance(road_time_points_sample, str):
                try:
                    road_time_points_sample = json.loads(road_time_points_sample)
                except Exception:
                    road_time_points_sample = []
            if not isinstance(road_time_points_sample, list):
                road_time_points_sample = []

            cv_points = spatial_points_sample if spatial_points_sample else road_time_points_sample
            map_points_source = "spatial" if spatial_points_sample else "road-time"

            braking = {"hard_braking_events": 0, "hard_braking_vehicles": 0}
            if hb_ctx.get("hb_table") and hb_ctx.get("geom_m_expr"):
                hb_dataset_clause = "AND p.dataset_id = %(cv_dataset_id)s" if hb_ctx["has_dataset_col"] and cv_dataset_id else ""
                hb_road_clause = (
                    f"AND {hb_ctx['road_segment_expr']} = wz.road_segment_id"
                    if hb_ctx["road_segment_expr"] != "NULL::text"
                    else ""
                )
                cur.execute(
                    f"""
                    WITH wz AS (
                      SELECT
                        %(road_segment_id)s::text AS road_segment_id,
                        CASE
                          WHEN %(geom_json)s IS NOT NULL
                            THEN ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geom_json)s), 4326), 26915)
                          ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%(wz_lon)s, %(wz_lat)s), 4326), 26915)
                        END AS wz_geom_m,
                        %(start_ts)s::timestamptz AS start_ts,
                        %(end_ts)s::timestamptz AS end_ts
                    ),
                    wz_window AS (
                      SELECT
                        road_segment_id,
                        wz_geom_m,
                        start_ts AS during_start,
                        end_ts AS during_end
                      FROM wz
                    ),
                    filtered AS (
                      SELECT
                        {hb_ctx["vehicle_id_expr"]} AS vehicle_id
                      FROM {hb_ctx["hb_table"]} p, wz_window wz
                      WHERE {hb_ctx["geom_m_expr"]} IS NOT NULL
                        {hb_dataset_clause}
                        AND p.ts IS NOT NULL
                        AND wz.during_start IS NOT NULL
                        AND wz.during_end IS NOT NULL
                        {hb_road_clause}
                        AND p.ts BETWEEN wz.during_start AND wz.during_end
                        AND {hb_ctx["geom_m_expr"]} && ST_Expand(wz.wz_geom_m, %(distance_m)s)
                        AND ST_DWithin({hb_ctx["geom_m_expr"]}, wz.wz_geom_m, %(distance_m)s)
                    )
                    SELECT
                      COUNT(*) AS hard_braking_events,
                      COUNT(DISTINCT vehicle_id) AS hard_braking_vehicles
                    FROM filtered
                    """,
                    params,
                )
                braking = cur.fetchone() or braking
            else:
                logger.info("Workzone analysis: hard-brake source table unavailable in active schema")

            if crash_dataset_id:
                cur.execute(
                    f"""
                    WITH wz AS (
                      SELECT
                        CASE
                          WHEN %(geom_json)s IS NOT NULL
                            THEN ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geom_json)s), 4326), 26915)
                          ELSE ST_Transform(ST_SetSRID(ST_MakePoint(%(wz_lon)s, %(wz_lat)s), 4326), 26915)
                        END AS wz_geom_m,
                        %(start_ts)s::timestamptz AS during_start,
                        %(end_ts)s::timestamptz AS during_end
                    ),
                    filtered AS (
                      SELECT
                        e.lat AS latitude,
                        e.lon AS longitude,
                        e.ts AS timestamp,
                        e.road_segment_id,
                        COALESCE(NULLIF(e.props->>'severity',''), NULLIF(e.props->>'crashSeverity','')) AS severity,
                        NULLIF(e.props->>'accident_date','') AS accident_date,
                        NULLIF(e.props->>'accident_time','') AS accident_time
                      FROM {APP_EVENTS} e
                      CROSS JOIN wz
                      WHERE e.dataset_id = %(crash_dataset_id)s
                        AND e.geom_m IS NOT NULL
                        AND e.ts IS NOT NULL
                        AND wz.during_start IS NOT NULL
                        AND wz.during_end IS NOT NULL
                        AND e.ts BETWEEN wz.during_start AND wz.during_end
                        AND e.geom_m && ST_Expand(wz.wz_geom_m, 500.0)
                        AND ST_DWithin(e.geom_m, wz.wz_geom_m, 500.0)
                    ),
                    sampled AS (
                      SELECT *
                      FROM filtered
                      ORDER BY timestamp NULLS LAST
                      LIMIT %(map_point_limit)s
                    )
                    SELECT
                      (SELECT COUNT(*) FROM filtered) AS crash_total,
                      COALESCE((SELECT jsonb_agg(to_jsonb(s)) FROM sampled s), '[]'::jsonb) AS crash_points
                    """,
                    {**params, "crash_dataset_id": crash_dataset_id},
                )
                crash_summary = cur.fetchone() or {}
                crash_points_total = int(crash_summary.get("crash_total") or 0)
                crash_points = crash_summary.get("crash_points") or []
                if isinstance(crash_points, str):
                    try:
                        crash_points = json.loads(crash_points)
                    except Exception:
                        crash_points = []
                if not isinstance(crash_points, list):
                    crash_points = []

        with_points = summary.get("with_points") or 0
        with_vehicles = summary.get("with_vehicles") or 0
        with_avg_speed = summary.get("with_avg_speed")
        spatial_points = summary.get("spatial_points") or 0
        conflated_points = summary.get("conflated_points") or 0
        effective_during_start = summary.get("during_start") or start_ts
        effective_during_end = summary.get("during_end") or end_ts
        hb_events = braking.get("hard_braking_events") or 0
        hb_vehicles = braking.get("hard_braking_vehicles") or 0

        road_name = wz_row.get("road_name") or "Unknown road"
        response_lines = [
            f"Workzone analysis (CV within {distance_m:.0f}m, same road segment, full workzone overlap):",
            f"- Road: {road_name} ({road_segment_id})",
            f"- Workzone window: {effective_during_start} → {effective_during_end}",
            f"- Nearby CV points in spatial buffer: {int(spatial_points):,}",
            f"- Conflated CV points in corridor buffer: {int(conflated_points):,}",
            f"- During workzone window: {int(with_points):,} points across {int(with_vehicles):,} vehicles",
            f"- Avg speed (during): {with_avg_speed:.1f} mph" if with_avg_speed is not None else "- Avg speed (during): N/A",
            f"- Hard braking events (<= -0.2g): {int(hb_events):,} across {int(hb_vehicles):,} vehicles (during window)",
            f"- Map points source: {'nearby spatial subset' if map_points_source == 'spatial' else 'road-time fallback'}",
        ]
        if crash_points_total:
            response_lines.append(f"- Crashes within 500m during window: {crash_points_total:,}")
            if crash_points_total > len(crash_points):
                response_lines.append(f"- Crash map payload sampled: showing {len(crash_points):,} of {crash_points_total:,}")
        else:
            response_lines.append("- Crashes within 500m during window: 0")

        chart_payload = [{
            "type": "bar",
            "title": "Workzone Corridor Speed",
            "xLabel": "Segment",
            "yLabel": "Avg speed (mph)",
            "xValues": ["During workzone"],
            "series": [
                {"label": "Avg speed", "values": [
                    float(with_avg_speed) if with_avg_speed is not None else None,
                ]},
            ],
            "meta": {
                "chartRole": "workzone_speed_compare",
                "description": "Average speed during the workzone overlap window.",
            },
        }]

        wz_row["dataset_id"] = workzone_dataset_id
        wz_row["road_segment_id"] = road_segment_id
        wz_lines_payload = _make_workzone_map_payload(
            [wz_row],
            label="Workzone",
            exclusive=False,
            dataset_id=workzone_dataset_id,
        )

        map_points = [
            {
                **p,
                "type": "Vehicle",
                "point_type": "Traffic",
            } for p in cv_points
        ] + [
            {
                **p,
                "type": "Crash",
                "point_type": "Crash",
            } for p in crash_points
        ]

        response_text = "\n".join(response_lines)
        result_payload = {
            "status": "success",
            "response": response_text,
            "mapSelection": {
                "label": "Workzone + nearby CV points",
                "count": len(map_points),
                "points": map_points,
                "lines": wz_lines_payload.get("lines", []),
                "overlay": False,
            },
            "chartPayload": chart_payload,
        }
        _persist_analysis_context(
            _build_workzone_analysis_context(
                response_text=response_text,
                payload=payload,
                summary=summary,
                braking=braking,
                crash_points_total=crash_points_total,
                road_segment_id=str(road_segment_id) if road_segment_id is not None else None,
                road_name=str(road_name) if road_name is not None else None,
                distance_m=distance_m,
                effective_during_start=effective_during_start,
                effective_during_end=effective_during_end,
                cv_dataset_id=cv_dataset_id,
                crash_dataset_id=crash_dataset_id,
                workzone_dataset_id=workzone_dataset_id,
            ),
            session_id=session_id,
        )
        return result_payload
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except psycopg2.errors.QueryCanceled:
        raise HTTPException(
            status_code=504,
            detail=(
                "Workzone analysis timed out. Try a shorter distance, a narrower date range, "
                "or a less busy road segment."
            ),
        )
    except Exception as e:
        logger.error(f"Workzone analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

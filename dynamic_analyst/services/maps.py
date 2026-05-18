"""Map payload helper services."""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional


# Matches bare datetime strings with NO timezone suffix (space or T separator).
# e.g. "2025-07-16 04:35:00" or "2025-07-16T04:35:00.123456"
_NAIVE_DT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$')


def _parse_props(value: Any) -> dict[str, Any]:
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


def _parse_loose_json(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return None


def _canonical_entity_fields(entity_type: Any) -> tuple[str, str, str]:
    raw = str(entity_type or "").strip().lower()
    compact = "".join(ch for ch in raw if ch.isalnum())

    if raw in {"crash", "crashes"} or "crash" in compact or "accident" in compact:
        return "Crash", "Crash", "crash"
    if (
        raw in {"hard_braking", "hard braking", "hard_brake", "hardbrake", "hardbraking", "hb"}
        or "hardbrake" in compact
        or "hardbraking" in compact
    ):
        return "HardBrake", "HardBrake", "hard_braking"
    if raw in {"workzone", "work_zone"} or "workzone" in compact:
        return "Workzone", "Workzone", "workzone"
    if raw in {"traffic", "vehicle", "cv"}:
        return "Vehicle", "Traffic", "traffic"
    if raw:
        return raw.title(), "Traffic", raw
    return "Vehicle", "Traffic", "traffic"


def _extract_workzone_lines(geometry_value: Any) -> list[list[list[float]]]:
    geometry = _parse_loose_json(geometry_value)
    if not isinstance(geometry, dict):
        return []
    coords = geometry.get("coordinates")
    if not coords:
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [coords]
    if geometry_type == "MultiLineString":
        return list(coords)
    return []


def _extract_workzone_point(geometry_value: Any) -> Optional[tuple[float, float]]:
    geometry = _parse_loose_json(geometry_value)
    if not isinstance(geometry, dict):
        return None

    geometry_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if geometry_type == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        try:
            return float(coords[0]), float(coords[1])
        except Exception:
            return None

    if geometry_type == "MultiPoint" and isinstance(coords, (list, tuple)) and coords:
        first = coords[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            try:
                return float(first[0]), float(first[1])
            except Exception:
                return None
    return None


def make_workzone_map_payload(
    rows: list[dict[str, Any]],
    label: str = "Workzone Map",
    exclusive: bool = True,
    dataset_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build workzone map payload for map rendering.

    Primary visualization is line-based (workzone geometry), but if a row has
    no usable line geometry we emit a point fallback so map requests still show
    spatial context for that workzone.
    """
    lines: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        line_sets = _extract_workzone_lines(row.get("geometry"))
        core = _parse_loose_json(row.get("core_details")) or {}
        road_name = row.get("road_name")
        if not road_name:
            road_names = core.get("road_names")
            if isinstance(road_names, list) and road_names:
                road_name = " / ".join([str(value) for value in road_names if value])

        description = core.get("description")
        status = row.get("vehicle_impact") or core.get("event_type") or row.get("status")
        road_segment_id = row.get("road_segment_id")
        ds_id = row.get("dataset_id") or dataset_id
        row_id = str(row.get("id") or f"workzone-{idx}")
        has_line = False

        for line_idx, coords in enumerate(line_sets):
            clean_coords = []
            for pair in coords:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                try:
                    lon = float(pair[0])
                    lat = float(pair[1])
                except Exception:
                    continue
                clean_coords.append([lon, lat])
            if len(clean_coords) < 2:
                continue
            has_line = True
            lines.append(
                {
                    # Keep id equal to raw event id so workzone-analyze can
                    # use it directly (endpoint expects events.id-compatible value).
                    "id": row_id,
                    "lineIndex": line_idx,
                    "coordinates": clean_coords,
                    "roadName": road_name or "Unknown",
                    "roadSegmentId": road_segment_id,
                    "datasetId": ds_id,
                    "status": status,
                    "description": description,
                    "startDate": row.get("start_date"),
                    "endDate": row.get("end_date"),
                    "exclusive": bool(exclusive),
                }
            )

        if not has_line:
            lon = row.get("lon") if row.get("lon") is not None else row.get("longitude")
            lat = row.get("lat") if row.get("lat") is not None else row.get("latitude")
            if lon is None or lat is None:
                geom_point = _extract_workzone_point(row.get("geometry"))
                if geom_point:
                    lon, lat = geom_point
            try:
                lon_f = float(lon) if lon is not None else None
                lat_f = float(lat) if lat is not None else None
            except Exception:
                lon_f = None
                lat_f = None

            if lon_f is not None and lat_f is not None:
                points.append(
                    {
                        "id": row_id,
                        "type": "workzone",
                        "point_type": "workzone",
                        "latitude": lat_f,
                        "longitude": lon_f,
                        "road_name": road_name or "Unknown",
                        "roadName": road_name or "Unknown",
                        "road_segment_id": road_segment_id,
                        "roadSegmentId": road_segment_id,
                        "dataset_id": ds_id,
                        "datasetId": ds_id,
                        "status": status,
                        "description": description,
                        "start_date": row.get("start_date"),
                        "end_date": row.get("end_date"),
                    }
                )

    total_features = len(lines) + len(points)
    return {
        "label": f"{label} ({len(lines)} lines, {len(points)} points)",
        "count": total_features,
        "lines": lines,
        "points": points,
    }


def _extract_road_name(props: dict[str, Any], road_segment_id: Any = None) -> str:
    """Extract road name from props/attrs trying multiple key variants."""
    for key in ("road_name", "road", "RoadName", "roadName"):
        val = props.get(key)
        if val:
            return str(val)
    # Fallback: road_segment_id often IS the road name (e.g. "US 61 N")
    if road_segment_id:
        return str(road_segment_id)
    return ""


def _extract_speed(props: dict[str, Any]) -> Optional[float]:
    """Extract speed from props/attrs trying multiple key variants."""
    for key in ("speed", "SpeedMPH", "speed_mph", "speedMPH"):
        val = props.get(key)
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _extract_speed_limit(props: dict[str, Any]) -> Optional[float]:
    """Extract speed limit from props/attrs trying multiple key variants."""
    for key in ("SpeedLimitMPH", "speedLimit", "speed_limit_mph", "speed_limit",
                "SpeedLimit", "at_loc_speed_limit"):
        val = props.get(key)
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _first_non_empty_str(*values: Any) -> Optional[str]:
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def _extract_crash_date(props: dict[str, Any]) -> Optional[str]:
    return _first_non_empty_str(
        props.get("_event_date_norm"),
        props.get("accident_date"),
        props.get("event_date"),
        props.get("crash_date"),
    )


def _extract_crash_time(props: dict[str, Any]) -> Optional[str]:
    return _first_non_empty_str(
        props.get("_event_time_norm"),
        props.get("accident_time"),
        props.get("event_time"),
        props.get("crash_time"),
    )


def _ensure_utc_event_ts(event_ts: Any) -> Optional[str]:
    """Stringify event timestamp, treating naive datetimes as UTC.

    psycopg2 returns PostgreSQL `timestamp` (no tz) columns as naive Python
    datetimes. Without the UTC offset in the string, JavaScript's new Date()
    interprets the value as *local* time (CDT = UTC-5), shifting display by
    5–6 hours. Annotating naive datetimes as UTC ensures "+00:00" appears in
    the string and the browser converts correctly to local time.
    """
    if event_ts is None:
        return None
    if isinstance(event_ts, datetime):
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        return event_ts.isoformat()
    text = str(event_ts).strip()
    if not text or text in ("NaT", "None", "null"):
        return None
    # Bare datetime string with no timezone — annotate as UTC and normalize separator
    if _NAIVE_DT_RE.match(text):
        normalized = text[:10] + 'T' + text[11:] if text[10] == ' ' else text
        return normalized + '+00:00'
    return text


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_conflation_map_points(
    match_df: Any,
    left_info: dict[str, Any],
    right_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build map points from conflation match rows."""
    points: list[dict[str, Any]] = []
    left_seen: set[Any] = set()
    right_seen: set[Any] = set()

    # Pre-pass: build partner ID lookup tables for pair linkage
    left_to_rights: dict[Any, list[str]] = {}
    right_to_lefts: dict[Any, list[str]] = {}
    for _, row in match_df.iterrows():
        lid = row["left_id"]
        rid = row["right_id"]
        rid_str = str(rid)
        if lid not in left_to_rights:
            left_to_rights[lid] = []
        if rid_str not in left_to_rights[lid]:
            left_to_rights[lid].append(rid_str)
        lid_str = str(lid)
        if rid not in right_to_lefts:
            right_to_lefts[rid] = []
        if lid_str not in right_to_lefts[rid]:
            right_to_lefts[rid].append(lid_str)

    has_road_name_cols = "left_road_name" in match_df.columns

    for _, row in match_df.iterrows():
        left_id = row["left_id"]
        if left_id not in left_seen:
            left_seen.add(left_id)
            props = _parse_props(row.get("left_props"))
            left_type, left_point_type, left_dataset_type = _canonical_entity_fields(left_info.get("entity_type"))
            try:
                left_lat = float(row["left_latitude"])
                left_lon = float(row["left_longitude"])
            except Exception:
                left_lat = None
                left_lon = None
            if left_lat is not None and left_lon is not None:
                # Use pre-extracted columns from SQL if available, else parse props
                if has_road_name_cols:
                    road_name = row.get("left_road_name") or ""
                else:
                    road_name = _extract_road_name(props, row.get("left_road_segment_id"))
                speed_val = row.get("left_speed")
                if speed_val is None:
                    speed_val = _extract_speed(props)
                speed_limit_val = row.get("left_speed_limit")
                if speed_limit_val is None:
                    speed_limit_val = _extract_speed_limit(props)
                # Fallback to partner's speed limit — crash and hard-brake are co-located
                if speed_limit_val is None:
                    partner_sl = row.get("right_speed_limit")
                    if partner_sl is None:
                        partner_sl = _extract_speed_limit(_parse_props(row.get("right_props")))
                    speed_limit_val = partner_sl
                acc_x_val = row.get("left_acc_x")
                if acc_x_val is None:
                    acc_x_val = props.get("acc_x", props.get("accx", props.get("AccX")))
                acc_y_val = row.get("left_acc_y")
                if acc_y_val is None:
                    acc_y_val = props.get("acc_y", props.get("accy", props.get("AccY")))
                event_ts = row.get("left_time")
                event_ts_text = _ensure_utc_event_ts(event_ts)

                point = {
                    "latitude": left_lat,
                    "longitude": left_lon,
                    "type": left_type,
                    "point_type": left_point_type,
                    "datasetType": left_dataset_type,
                    "entity_type": left_dataset_type,
                    "id": str(left_id),
                    "primary_id": str(left_id),
                    "matched": True,
                    "roadName": road_name or "Unknown",
                    "road_name": road_name or "Unknown",
                }
                if event_ts_text:
                    point["timestamp"] = event_ts_text
                    point["eventDate"] = event_ts_text
                if speed_val is not None:
                    try:
                        point["speed"] = float(speed_val)
                    except (TypeError, ValueError):
                        pass
                if speed_limit_val is not None:
                    try:
                        point["SpeedLimitMPH"] = float(speed_limit_val)
                        point["speedLimit"] = float(speed_limit_val)
                    except (TypeError, ValueError):
                        pass
                acc_x_num = _to_float(acc_x_val)
                if acc_x_num is not None:
                    point["acc_x"] = acc_x_num
                    point["accX"] = acc_x_num
                    point["decelerationG"] = acc_x_num
                acc_y_num = _to_float(acc_y_val)
                if acc_y_num is not None:
                    point["acc_y"] = acc_y_num
                    point["accY"] = acc_y_num
                for key in ("severity", "description", "status"):
                    if key in props and props[key]:
                        point[key] = props[key]
                if left_dataset_type == "crash":
                    crash_date = _extract_crash_date(props)
                    crash_time = _extract_crash_time(props)
                    if crash_date:
                        point["accident_date"] = crash_date
                    if crash_time:
                        point["accident_time"] = crash_time
                if "roadName" not in point:
                    road_name = point.get("road_name") or point.get("road")
                    if road_name:
                        point["roadName"] = road_name
                if "acc_x" in point or "acc_y" in point:
                    try:
                        point["acceleration"] = {
                            "x": float(point.get("acc_x") if point.get("acc_x") is not None else point.get("accX")),
                            "y": float(point.get("acc_y") if point.get("acc_y") is not None else point.get("accY") or 0.0),
                        }
                    except Exception:
                        pass
                if "decelerationG" not in point and point.get("acceleration", {}).get("x") is not None:
                    try:
                        point["decelerationG"] = float(point["acceleration"]["x"])
                    except Exception:
                        pass
                point["matched_partner_ids"] = left_to_rights.get(left_id, [])
                point["match_count"] = len(point["matched_partner_ids"])
                points.append(point)

        right_id = row["right_id"]
        if right_id not in right_seen:
            right_seen.add(right_id)
            props = _parse_props(row.get("right_props"))
            right_type, right_point_type, right_dataset_type = _canonical_entity_fields(right_info.get("entity_type"))
            try:
                right_lat = float(row["right_latitude"])
                right_lon = float(row["right_longitude"])
            except Exception:
                right_lat = None
                right_lon = None
            if right_lat is not None and right_lon is not None:
                if has_road_name_cols:
                    road_name = row.get("right_road_name") or ""
                else:
                    road_name = _extract_road_name(props, row.get("right_road_segment_id"))
                speed_val = row.get("right_speed")
                if speed_val is None:
                    speed_val = _extract_speed(props)
                speed_limit_val = row.get("right_speed_limit")
                if speed_limit_val is None:
                    speed_limit_val = _extract_speed_limit(props)
                # Fallback to partner's speed limit — crash and hard-brake are co-located
                if speed_limit_val is None:
                    partner_sl = row.get("left_speed_limit")
                    if partner_sl is None:
                        partner_sl = _extract_speed_limit(_parse_props(row.get("left_props")))
                    speed_limit_val = partner_sl
                acc_x_val = row.get("right_acc_x")
                if acc_x_val is None:
                    acc_x_val = props.get("acc_x", props.get("accx", props.get("AccX")))
                acc_y_val = row.get("right_acc_y")
                if acc_y_val is None:
                    acc_y_val = props.get("acc_y", props.get("accy", props.get("AccY")))
                event_ts = row.get("right_time")
                event_ts_text = _ensure_utc_event_ts(event_ts)

                point = {
                    "latitude": right_lat,
                    "longitude": right_lon,
                    "type": right_type,
                    "point_type": right_point_type,
                    "datasetType": right_dataset_type,
                    "entity_type": right_dataset_type,
                    "id": str(right_id),
                    "primary_id": str(right_id),
                    "matched": True,
                    "roadName": road_name or "Unknown",
                    "road_name": road_name or "Unknown",
                }
                if event_ts_text:
                    point["timestamp"] = event_ts_text
                    point["eventDate"] = event_ts_text
                if speed_val is not None:
                    try:
                        point["speed"] = float(speed_val)
                    except (TypeError, ValueError):
                        pass
                if speed_limit_val is not None:
                    try:
                        point["SpeedLimitMPH"] = float(speed_limit_val)
                        point["speedLimit"] = float(speed_limit_val)
                    except (TypeError, ValueError):
                        pass
                acc_x_num = _to_float(acc_x_val)
                if acc_x_num is not None:
                    point["acc_x"] = acc_x_num
                    point["accX"] = acc_x_num
                    point["decelerationG"] = acc_x_num
                acc_y_num = _to_float(acc_y_val)
                if acc_y_num is not None:
                    point["acc_y"] = acc_y_num
                    point["accY"] = acc_y_num
                for key in ("severity", "description", "status"):
                    if key in props and props[key]:
                        point[key] = props[key]
                if right_dataset_type == "crash":
                    crash_date = _extract_crash_date(props)
                    crash_time = _extract_crash_time(props)
                    if crash_date:
                        point["accident_date"] = crash_date
                    if crash_time:
                        point["accident_time"] = crash_time
                if "roadName" not in point:
                    road_name = point.get("road_name") or point.get("road")
                    if road_name:
                        point["roadName"] = road_name
                if "acc_x" in point or "acc_y" in point:
                    try:
                        point["acceleration"] = {
                            "x": float(point.get("acc_x") if point.get("acc_x") is not None else point.get("accX")),
                            "y": float(point.get("acc_y") if point.get("acc_y") is not None else point.get("accY") or 0.0),
                        }
                    except Exception:
                        pass
                if "decelerationG" not in point and point.get("acceleration", {}).get("x") is not None:
                    try:
                        point["decelerationG"] = float(point["acceleration"]["x"])
                    except Exception:
                        pass
                point["matched_partner_ids"] = right_to_lefts.get(right_id, [])
                point["match_count"] = len(point["matched_partner_ids"])
                points.append(point)

    return points


def save_conflation_map(
    match_df: Any,
    left_info: dict[str, Any],
    right_info: dict[str, Any],
    left_id: str,
    right_id: str,
) -> int:
    """Build and save conflation map payload; returns point count saved."""
    from ..session_state import save_map_for_session

    points = build_conflation_map_points(match_df, left_info=left_info, right_info=right_info)
    if not points:
        return 0

    save_map_for_session(
        {
            "label": (
                f"Conflation: {left_info.get('name', left_id)} + "
                f"{right_info.get('name', right_id)} ({len(points)} matched points)"
            ),
            "count": len(points),
            "points": points,
        },
        map_type="conflation",
    )
    return len(points)

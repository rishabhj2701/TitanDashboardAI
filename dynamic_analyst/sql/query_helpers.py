"""Shared query helper functions for SQL handlers."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ..column_intelligence import suggest_similar_columns


def _normalize_metric_key(raw: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").strip().lower())


def _groupby_columns_from_params(params: Optional[dict]) -> list[str]:
    """Accept common planner aliases for group-by column lists."""
    if not isinstance(params, dict):
        return []
    for key in ("group_by", "columns", "cols", "group_cols"):
        raw = params.get(key)
        if isinstance(raw, list):
            out = [str(item).strip() for item in raw if str(item or "").strip()]
            if out:
                return out
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
    return []


def _resolve_sql_column_key(col: str, col_map: dict) -> Optional[str]:
    if col in col_map:
        return col
    norm = _normalize_metric_key(col)
    if not norm:
        return None

    # Common aggregate aliases / variants the model emits.
    alias_norm_map = {
        "speedmean": "speed",
        "speedavg": "speed",
        "meanspeed": "speed",
        "avgspeed": "speed",
        "speedlimitmean": "SpeedLimitMPH",
        "speedlimitavg": "SpeedLimitMPH",
        "meanspeedlimit": "SpeedLimitMPH",
        "avgspeedlimit": "SpeedLimitMPH",
        "speedlimitmphmean": "SpeedLimitMPH",
        "speedlimitmphavg": "SpeedLimitMPH",
        "meanspeedlimitmph": "SpeedLimitMPH",
        "avgspeedlimitmph": "SpeedLimitMPH",
        "meanspeedoverlimit": "speed_over_limit",
        "avgspeedoverlimit": "speed_over_limit",
        "speedoverlimitmean": "speed_over_limit",
        "speedoverlimitavg": "speed_over_limit",
        # road-stats schema column names for speed/limit (e.g. avg_speed_mph, speed_limit_mph)
        "avgspeedmph": "speed",
        "meanspeedmph": "speed",
        "speedmph": "speed",
        "speedlimitmph": "SpeedLimitMPH",
        "avglimitmph": "SpeedLimitMPH",
        # point_count is the road-stats proxy for traffic volume; map to count sentinel
        "pointcount": "count",
        "point_count": "count",
        "trafficcount": "count",
        "trafficvolume": "count",
        "traffic_volume": "count",
        "trafficentries": "count",
        "traffic_entries": "count",
        "entries": "count",
        "numpoints": "count",
        "num_points": "count",
        "observations": "count",
        "records": "count",
        "vehicles": "count",
        "vehiclecount": "count",
        "vehicle_count": "count",
    }
    canonical = alias_norm_map.get(norm)
    if canonical and canonical in col_map:
        return canonical

    # Expression-style speed-over-limit references.
    if "speed_over_limit" in col_map:
        derived_expr_keys = {
            "speedmeanspeedlimitmean",
            "meanspeedmeanspeedlimitmean",
            "avgspeedavgspeedlimit",
            "avgspeedspeedlimitavg",
            "speedavgspeedlimitavg",
            "speedmeanspeedlimitavg",
            "speedmeanspeedlimitmphmean",
            "avgspeedspeedlimitmphavg",
        }
        if norm in derived_expr_keys:
            return "speed_over_limit"
        text = str(col or "").strip().lower()
        if (
            "speed" in norm
            and "limit" in norm
            and ("mean" in norm or "avg" in norm)
            and ("-" in text or "minus" in text)
        ):
            return "speed_over_limit"

    # Last chance: exact normalized-key match against available columns.
    for key in col_map.keys():
        if _normalize_metric_key(key) == norm:
            return key
    return None


def _schema_safe_column(name: str, schema_cols: set[str]) -> Optional[str]:
    """
    Find the actual column name in schema_cols that matches the requested name.
    Prioritizes deterministic matching only:
    1) exact
    2) case-insensitive exact
    3) normalized exact (space/hyphen/underscore variants)
    """
    if not name or not schema_cols:
        return None
    if name in schema_cols:
        return name
    name_lower = name.lower()
    for col in schema_cols:
        if col.lower() == name_lower:
            return col
    name_normalized = name_lower.replace(" ", "_").replace("-", "_")
    for col in schema_cols:
        col_normalized = col.lower().replace(" ", "_").replace("-", "_")
        if col_normalized == name_normalized:
            return col
    return None


def _safe_float8_expr(text_expr: str) -> str:
    """
    Convert a text SQL expression to float8 safely by guarding with a numeric regex.
    Non-numeric values evaluate to NULL instead of raising SQL cast errors.
    """
    return (
        f"CASE WHEN ({text_expr}) ~ '^-?[0-9]+(\\.[0-9]+)?$' "
        f"THEN ({text_expr})::float8 END"
    )


def _get_column_suggestions(failed_column: str, schema_cols: set[str]) -> str:
    """Get suggestion message when a column lookup fails."""
    if not schema_cols:
        return ""
    suggestions = suggest_similar_columns(failed_column, schema_cols, max_suggestions=5)
    if suggestions:
        suggestion_strs = [f"'{col}' ({score:.0%})" for col, score in suggestions if score > 0.3]
        if suggestion_strs:
            return f" Did you mean: {', '.join(suggestion_strs[:3])}?"
    return ""


def _normalize_sort(sort_req: Optional[dict]) -> tuple[list[str], list[bool]]:
    if not sort_req:
        return [], []
    by = sort_req.get("by") or sort_req.get("sort_by") or sort_req.get("column")
    if by is None:
        return [], []
    cols: list[str] = []
    per_col_dirs: list[Optional[bool]] = []
    if isinstance(by, str):
        cols = [by]
        per_col_dirs = [None]
    elif isinstance(by, (list, tuple)):
        for item in by:
            if isinstance(item, str):
                cols.append(item)
                per_col_dirs.append(None)
                continue
            if isinstance(item, dict):
                col = item.get("column") or item.get("by")
                if not isinstance(col, str) or not col.strip():
                    raise ValueError("sort.by dict entries require a non-empty 'column'")
                cols.append(col.strip())
                direction = item.get("direction")
                if direction is None:
                    direction = item.get("order")
                if isinstance(direction, str):
                    d = direction.strip().lower()
                    if d in ("desc", "descending", "-1"):
                        per_col_dirs.append(False)
                    elif d in ("asc", "ascending", "1"):
                        per_col_dirs.append(True)
                    else:
                        per_col_dirs.append(None)
                elif isinstance(direction, bool):
                    per_col_dirs.append(bool(direction))
                elif isinstance(direction, (int, float)):
                    per_col_dirs.append(direction >= 0)
                else:
                    per_col_dirs.append(None)
                continue
            raise ValueError("sort.by list entries must be strings or {column, direction} objects")
    else:
        raise ValueError("sort.by must be a string or list")
    # Support both "ascending": bool and "order": "asc"/"desc"
    order_str = sort_req.get("order")
    if isinstance(order_str, str):
        ascending = order_str.strip().lower() not in ("desc", "descending", "-1")
    else:
        ascending = sort_req.get("ascending", True)
    if isinstance(ascending, (list, tuple)):
        if len(ascending) != len(cols):
            raise ValueError("sort.ascending length must match sort.by length")
        dirs = []
        for i, v in enumerate(ascending):
            dirs.append(per_col_dirs[i] if per_col_dirs[i] is not None else bool(v))
    else:
        default_dir = bool(ascending)
        dirs = [d if d is not None else default_dir for d in per_col_dirs]
    return cols, dirs


def _sort_needs_roads(sort_req: Optional[dict]) -> bool:
    cols, _ = _normalize_sort(sort_req)
    return any(c in ("road", "road_name", "name") for c in cols)


def _has_count_aggregation(aggs: Dict[str, Any]) -> bool:
    for raw_key, raw_val in (aggs or {}).items():
        if raw_key == "count":
            return True
        if isinstance(raw_val, dict):
            fn = str(
                raw_val.get("fn")
                or raw_val.get("function")
                or raw_val.get("agg")
                or ""
            ).strip().lower()
            if fn == "count":
                return True
        else:
            if str(raw_val or "").strip().lower() == "count":
                return True
    return False


__all__ = [
    "_get_column_suggestions",
    "_has_count_aggregation",
    "_normalize_metric_key",
    "_normalize_sort",
    "_resolve_sql_column_key",
    "_safe_float8_expr",
    "_schema_safe_column",
    "_sort_needs_roads",
]

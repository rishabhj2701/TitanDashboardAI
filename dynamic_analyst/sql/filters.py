"""Filter and road-scope helper functions shared by SQL handlers."""

from __future__ import annotations

import re
from typing import Any, Optional

from .query_helpers import _resolve_sql_column_key


def _needs_roads_join(conditions: list[dict]) -> bool:
    for c in conditions or []:
        if "conditions" in c:
            if _needs_roads_join(c.get("conditions") or []):
                return True
            continue
        col = (c.get("column") or "").strip()
        if col in ("road", "road_name", "name"):
            return True
    return False


def _build_road_name_patterns(raw: str) -> list[str]:
    text = (raw or "").strip().upper()
    if not text:
        return []
    text = " ".join(text.split())
    patterns = {f"%{text}%", f"%{text.replace('-', ' ')}%"}

    def _add_route_patterns(prefixes: list[str], num: str) -> None:
        for p in prefixes:
            patterns.add(f"%{p} {num}%")
            patterns.add(f"%{p}-{num}%")

    match_i = re.search(r"(?:I|IS|INTERSTATE)\s*-?\s*([0-9A-Z]+)", text)
    if match_i:
        _add_route_patterns(["IS", "I", "INTERSTATE"], match_i.group(1))

    match_us = re.search(r"(?:US|U\.S\.|U S)\s*-?\s*([0-9A-Z]+)", text)
    if match_us:
        _add_route_patterns(["US", "U.S."], match_us.group(1))

    match_mo = re.search(r"(?:MO|STATE|SR|HIGHWAY|HWY)\s*-?\s*([0-9A-Z]+)", text)
    if match_mo:
        _add_route_patterns(["MO", "STATE", "SR", "HIGHWAY", "HWY"], match_mo.group(1))

    return sorted(patterns)


def _compile_filter(
    conditions: list[dict],
    mode: str = "and",
    col_map: Optional[dict] = None,
) -> tuple[str, list, bool]:
    """
    Returns: (sql_clause_without_where, params, needs_roads_join)
    Supports: >, <, >=, <=, ==, !=, in, not in, contains, startswith, endswith,
              between, isnull, notnull
    """
    if col_map is None:
        raise ValueError("col_map is required for _compile_filter")

    mode = (mode or "and").lower()
    joiner = " OR " if mode == "or" else " AND "
    parts = []
    params: list = []
    needs_join = _needs_roads_join(conditions)

    for cond in conditions or []:
        if "conditions" in cond:
            sub_mode = (cond.get("mode") or "and").lower()
            sub_clause, sub_params, sub_needs_join = _compile_filter(
                cond.get("conditions") or [],
                sub_mode,
                col_map,
            )
            needs_join = needs_join or sub_needs_join
            if sub_clause:
                parts.append(f"({sub_clause})")
                params.extend(sub_params)
            continue
        col = (cond.get("column") or "").strip()
        op = (cond.get("operator") or "").strip().lower()
        val = cond.get("value", None)

        resolved_col = _resolve_sql_column_key(col, col_map)
        if resolved_col is None:
            raise ValueError(f"Unsupported column for SQL mode: '{col}'. Check available columns first.")
        expr = col_map[resolved_col]

        if op in (">", "<", ">=", "<=", "==", "!=", "="):
            sql_op = "=" if op in ("==", "=") else op
            parts.append(f"{expr} {sql_op} %s")
            params.append(val)
        elif op in ("ilike", "like"):
            parts.append(f"CAST({expr} AS text) ILIKE %s")
            params.append(val)
        elif op == "contains":
            if resolved_col in ("road", "road_name", "name") and isinstance(val, str):
                road_patterns = _build_road_name_patterns(val)
                if road_patterns:
                    road_clauses = [f"CAST({expr} AS text) ILIKE %s" for _ in road_patterns]
                    parts.append("(" + " OR ".join(road_clauses) + ")")
                    params.extend(road_patterns)
                else:
                    parts.append(f"CAST({expr} AS text) ILIKE %s")
                    params.append(f"%{val}%")
            else:
                parts.append(f"CAST({expr} AS text) ILIKE %s")
                params.append(f"%{val}%")
        elif op == "startswith":
            parts.append(f"CAST({expr} AS text) ILIKE %s")
            params.append(f"{val}%")
        elif op == "endswith":
            parts.append(f"CAST({expr} AS text) ILIKE %s")
            params.append(f"%{val}")
        elif op == "between":
            if not (isinstance(val, (list, tuple)) and len(val) == 2):
                raise ValueError("between operator requires value=[low, high]")
            parts.append(f"{expr} BETWEEN %s AND %s")
            params.extend([val[0], val[1]])
        elif op == "in":
            if not isinstance(val, (list, tuple)) or len(val) == 0:
                raise ValueError("in operator requires a non-empty list value")
            placeholders = ",".join(["%s"] * len(val))
            parts.append(f"{expr} IN ({placeholders})")
            params.extend(list(val))
        elif op == "not in":
            if not isinstance(val, (list, tuple)) or len(val) == 0:
                raise ValueError("not in operator requires a non-empty list value")
            placeholders = ",".join(["%s"] * len(val))
            parts.append(f"{expr} NOT IN ({placeholders})")
            params.extend(list(val))
        elif op == "isnull":
            parts.append(f"{expr} IS NULL")
        elif op == "notnull":
            parts.append(f"{expr} IS NOT NULL")
        else:
            raise ValueError(f"Unsupported operator for SQL mode: '{op}'")

    clause = joiner.join(parts) if parts else ""
    return clause, params, needs_join


def _sanitize_sql_filters(conditions: list[dict]) -> tuple[list[dict], int]:
    """
    Remove non-SQL dynamic placeholders emitted by the model, e.g.
    {"value": {"_from_previous_step": "...", "column": "..."}}.
    Returns (clean_conditions, dropped_count).
    """
    cleaned: list[dict] = []
    dropped = 0
    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        if "conditions" in cond:
            nested, nested_dropped = _sanitize_sql_filters(cond.get("conditions") or [])
            dropped += nested_dropped
            if nested:
                rebuilt = dict(cond)
                rebuilt["conditions"] = nested
                cleaned.append(rebuilt)
            continue
        if any("from_previous_step" in str(k) for k in cond.keys()):
            dropped += 1
            continue
        value = cond.get("value")
        if isinstance(value, dict) and value.get("_from_previous_step"):
            dropped += 1
            continue
        op = (cond.get("operator") or "").strip().lower()
        if op in ("in", "not in", "between") and not isinstance(value, (list, tuple)):
            dropped += 1
            continue
        cleaned.append(cond)
    return cleaned, dropped


def _parse_near_filter_params(value: Any) -> dict:
    params = {
        "distance_m": 200.0,
        "window_minutes": 60,
        "dataset_id": None,
    }
    if isinstance(value, dict):
        if value.get("distance_m") is not None:
            params["distance_m"] = float(value["distance_m"])
        if value.get("window_minutes") is not None:
            params["window_minutes"] = int(value["window_minutes"])
        if value.get("dataset_id"):
            params["dataset_id"] = value.get("dataset_id")
    elif isinstance(value, bool):
        return params
    elif isinstance(value, (int, float)):
        params["distance_m"] = float(value)
    return params


def _extract_near_filters(filters: list) -> tuple[list, Optional[dict], Optional[dict]]:
    remaining: list = []
    near_crash: Optional[dict] = None
    near_workzone: Optional[dict] = None

    def _walk(cond: Any) -> Optional[dict]:
        nonlocal near_crash, near_workzone, remaining
        if not isinstance(cond, dict):
            return None
        if "conditions" in cond:
            kept: list = []
            for sub in (cond.get("conditions") or []):
                cleaned = _walk(sub)
                if cleaned is not None:
                    kept.append(cleaned)
            if kept:
                return {
                    "conditions": kept,
                    "mode": cond.get("mode") or "and",
                }
            return None
        col = cond.get("column")
        if col == "near_crash":
            near_crash = _parse_near_filter_params(cond.get("value"))
            return None
        if col == "near_workzone":
            near_workzone = _parse_near_filter_params(cond.get("value"))
            return None
        return cond

    for cond in (filters or []):
        cleaned = _walk(cond)
        if cleaned is not None:
            remaining.append(cleaned)

    return remaining, near_crash, near_workzone


def _has_hard_brake_filter(filters: list) -> bool:
    for cond in filters or []:
        if not isinstance(cond, dict):
            continue
        if "conditions" in cond:
            if _has_hard_brake_filter(cond.get("conditions") or []):
                return True
            continue
        col = cond.get("column")
        op = (cond.get("operator") or "").lower()
        val = cond.get("value")
        if col in ("acc_x", "AccX") and op in ("<", "<="):
            try:
                if float(val) <= -0.2:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _normalize_road_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _normalize_road_scope_search_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^['\"]+|['\"]+$", "", text)
    text = re.sub(r"[%_*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _collect_road_filter_scope_values(
    conditions: list[dict],
    exact_out: list[str],
    search_out: list[str],
) -> None:
    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        nested = cond.get("conditions")
        if isinstance(nested, list):
            _collect_road_filter_scope_values(nested, exact_out, search_out)
            continue
        col = (cond.get("column") or "").strip()
        if col not in ("road", "road_name", "name"):
            continue
        op = (cond.get("operator") or "").strip().lower()
        value = cond.get("value")
        if op in ("==", "=") and isinstance(value, str) and value.strip():
            exact_out.append(value.strip())
        elif op == "in" and isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    exact_out.append(item.strip())
        elif op in ("contains", "like", "ilike", "startswith", "endswith"):
            if isinstance(value, str) and value.strip():
                cleaned = _normalize_road_scope_search_value(value)
                if cleaned:
                    search_out.append(cleaned)


def _is_road_only_filter(conditions: list[dict]) -> bool:
    found_any = False
    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        nested = cond.get("conditions")
        if isinstance(nested, list):
            if not _is_road_only_filter(nested):
                return False
            found_any = True
            continue
        col = (cond.get("column") or "").strip()
        op = (cond.get("operator") or "").strip().lower()
        if col not in ("road", "road_name", "name"):
            return False
        if op not in ("==", "=", "in", "contains", "like", "ilike", "startswith", "endswith"):
            return False
        found_any = True
    return found_any


__all__ = [
    "_build_road_name_patterns",
    "_collect_road_filter_scope_values",
    "_compile_filter",
    "_extract_near_filters",
    "_has_hard_brake_filter",
    "_is_road_only_filter",
    "_needs_roads_join",
    "_normalize_road_value",
    "_parse_near_filter_params",
    "_sanitize_sql_filters",
]

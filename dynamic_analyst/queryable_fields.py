"""Helpers for dataset-level queryable field policy."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


_DEFAULT_QUERYABLE_BY_ENTITY: Dict[str, Dict[str, List[str]]] = {
    "crash": {
        "locked": ["event_date", "event_time", "latitude", "longitude"],
        "enabled": [
            "timestamp",
            "primary_id",
            "road_name",
            "road_segment_id",
            "severity",
            "accident_type",
            "number_killed",
            "number_injured",
            "no_of_vehicles",
            "city_name",
            "light_condition",
            "weather_cond_1",
            "weather_cond_2",
            "road_surface",
            "road_condition_1",
            "road_condition_2",
        ],
    },
    "workzone": {
        "locked": ["start_date", "end_date", "road_name"],
        "enabled": [
            "timestamp",
            "primary_id",
            "latitude",
            "longitude",
            "road_segment_id",
            "vehicle_impact",
            "location_method",
            "reduced_speed_limit_kph",
        ],
    },
    "event": {
        "locked": ["timestamp", "latitude", "longitude"],
        "enabled": [
            "event_date",
            "event_time",
            "primary_id",
            "road_name",
            "road_segment_id",
        ],
    },
}


def normalize_query_name(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _normalize_source(raw: Any) -> str:
    # Keep source_column close to incoming data keys, but remove dangerous chars.
    text = str(raw or "").strip()
    if not text:
        return ""
    if "'" in text or "\\" in text:
        return ""
    return text


def _entity_defaults(entity_type: Optional[str]) -> Dict[str, List[str]]:
    key = str(entity_type or "").strip().lower()
    return _DEFAULT_QUERYABLE_BY_ENTITY.get(key, _DEFAULT_QUERYABLE_BY_ENTITY["event"])


def build_default_queryable_fields(entity_type: Optional[str]) -> List[Dict[str, Any]]:
    defaults = _entity_defaults(entity_type)
    locked = [normalize_query_name(v) for v in defaults.get("locked") or []]
    enabled = [normalize_query_name(v) for v in defaults.get("enabled") or []]

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for name in locked + enabled:
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "query_name": name,
                "source_column": name,
                "enabled": True,
                "locked": name in locked,
            }
        )
    return out


def resolve_queryable_fields(
    entity_type: Optional[str],
    stored: Optional[Dict[str, Any]],
    *,
    available_sources: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    defaults = build_default_queryable_fields(entity_type)
    locked_defaults = {item["query_name"]: item for item in defaults if item.get("locked")}

    available_lookup: Dict[str, str] = {}
    if available_sources is not None:
        for source in available_sources:
            text = _normalize_source(source)
            if text:
                available_lookup[text.lower()] = text

    raw_fields = stored.get("fields") if isinstance(stored, dict) else None
    if not isinstance(raw_fields, list):
        raw_fields = []

    merged: Dict[str, Dict[str, Any]] = {}
    for raw in raw_fields:
        if not isinstance(raw, dict):
            continue
        source = _normalize_source(raw.get("source_column") or raw.get("column"))
        query_name = normalize_query_name(raw.get("query_name") or raw.get("name") or source)
        if not source or not query_name:
            continue

        if available_lookup:
            source = available_lookup.get(source.lower(), source)

        merged[query_name] = {
            "query_name": query_name,
            "source_column": source,
            "enabled": bool(raw.get("enabled", True)),
            "locked": bool(raw.get("locked", False)),
        }

    # Ensure required core fields are always present and enabled.
    for query_name, default_item in locked_defaults.items():
        current = merged.get(query_name)
        if current is None:
            merged[query_name] = dict(default_item)
            continue
        current["enabled"] = True
        current["locked"] = True
        if not current.get("source_column"):
            current["source_column"] = default_item["source_column"]

    if not merged:
        return defaults

    ordered: List[Dict[str, Any]] = []
    for item in defaults:
        key = item["query_name"]
        if key in merged:
            ordered.append(merged.pop(key))
    ordered.extend(sorted(merged.values(), key=lambda item: item.get("query_name", "")))
    return ordered


def build_queryable_alias_map(fields: List[Dict[str, Any]]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    for item in fields:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        source = _normalize_source(item.get("source_column"))
        query_name = normalize_query_name(item.get("query_name"))
        if not source or not query_name:
            continue
        alias_map[query_name] = source
        # Keep canonical source available as a query key as well.
        alias_map.setdefault(normalize_query_name(source), source)
    return alias_map

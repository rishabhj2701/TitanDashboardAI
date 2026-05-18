"""Default queryable-field policies for Iowa demo datasets."""

from __future__ import annotations

from typing import Any, Dict, List

IOWA_CRASH_QUERYABLE_FIELDS: List[Dict[str, Any]] = [
    {"query_name": "routeid", "source_column": "ROUTEID", "enabled": True},
    {"query_name": "road_segment_id", "source_column": "road_segment_id", "enabled": True},
    {"query_name": "road_name", "source_column": "road_segment_id", "enabled": True},
    {"query_name": "severity", "source_column": "CSEVERITY", "enabled": True},
    {"query_name": "cseverity", "source_column": "CSEVERITY", "enabled": True},
    {"query_name": "county", "source_column": "COUNTY", "enabled": True},
    {"query_name": "year", "source_column": "CRASH_YEAR", "enabled": True},
    {"query_name": "month", "source_column": "CRASHMONTH", "enabled": True},
    {"query_name": "day_of_week", "source_column": "CRASH_DAY", "enabled": True},
    {"query_name": "milepost", "source_column": "MEASURE", "enabled": True},
    {"query_name": "crash_time", "source_column": "TIMESTR", "enabled": True},
    {"query_name": "local_hour", "source_column": "local_hour", "enabled": True},
    {"query_name": "primary_id", "source_column": "CRASH_KEY", "enabled": True},
    {"query_name": "event_date", "source_column": "event_date", "enabled": True},
    {"query_name": "latitude", "source_column": "latitude", "enabled": True},
    {"query_name": "longitude", "source_column": "longitude", "enabled": True},
]

IOWA_CV_QUERYABLE_FIELDS: List[Dict[str, Any]] = [
    {"query_name": "route_id", "source_column": "route_id", "enabled": True},
    {"query_name": "routeid", "source_column": "route_id", "enabled": True},
    {"query_name": "road_segment_id", "source_column": "route_id", "enabled": True},
    {"query_name": "timestamp", "source_column": "timestamp_5min", "enabled": True},
    {"query_name": "timestamp_5min", "source_column": "timestamp_5min", "enabled": True},
    {"query_name": "start_ts", "source_column": "timestamp_5min", "enabled": True},
    {"query_name": "hour", "source_column": "hour", "enabled": True},
    {"query_name": "year", "source_column": "year", "enabled": True},
    {"query_name": "month", "source_column": "month", "enabled": True},
    {"query_name": "day", "source_column": "day", "enabled": True},
    {"query_name": "speed_mean_mph", "source_column": "speed_mean_mph", "enabled": True},
    {"query_name": "journeyid_nunique", "source_column": "journeyid_nunique", "enabled": True},
    {"query_name": "decel_03g_sum", "source_column": "decel_03g_sum", "enabled": True},
    {"query_name": "hard_brake_count", "source_column": "decel_03g_sum", "enabled": True},
]

CORE_CRASH_GROUPBY = ("routeid", "road_segment_id", "road_name", "severity", "cseverity", "county", "year", "month")


def merge_iowa_queryable_fields(
    entity_type: str,
    stored: Dict[str, Any] | None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Merge Iowa demo defaults into stored queryable_fields (keeps user toggles unless force)."""
    entity = str(entity_type or "").strip().lower()
    defaults = IOWA_CV_QUERYABLE_FIELDS if entity == "cv" else IOWA_CRASH_QUERYABLE_FIELDS
    if entity not in {"cv", "crash", "event"}:
        return stored if isinstance(stored, dict) else {}

    existing = {}
    if isinstance(stored, dict):
        for raw in stored.get("fields") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("query_name") or "").strip()
            if name:
                existing[name] = dict(raw)

    enabled_count = sum(1 for item in existing.values() if bool(item.get("enabled", True)))
    if not force and enabled_count >= 5:
        return stored if isinstance(stored, dict) else {"fields": list(existing.values())}

    for item in defaults:
        name = str(item.get("query_name") or "").strip()
        if not name:
            continue
        if force or name not in existing:
            existing[name] = {**item, "enabled": True}

    return {
        "entity_type": entity,
        "fields": list(existing.values()),
    }

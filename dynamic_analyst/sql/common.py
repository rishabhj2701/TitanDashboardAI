"""Shared SQL utilities used by domain-specific SQL handlers."""

from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any, Dict, List, Optional

import pandas as pd
from psycopg2.extras import RealDictCursor

from ..queryable_fields import (
    build_queryable_alias_map,
    normalize_query_name,
    resolve_queryable_fields,
)
from ..storage.postgis.table_names import APP_DATASETS
from ..session_state import get_active_session, get_active_user, save_map_for_session
from .catalog import (
    _db_conn,
    _active_cv_run_context,
    _cv_relation_candidates,
    _first_existing_relation,
    _relation_has_rows,
    _resolve_cv_base_table,
    _get_event_schema_columns,
    _latest_dataset_id_from_relation,
    _latest_event_dataset_id,
    _lookup_pedestrian_accident_type_codes,
    _resolve_cv_enrichment_sql,
    _resolve_event_dataset_id,
    _resolve_hard_brake_table,
    _resolve_point_coord_exprs,
    _resolve_traffic_sql_col_map as _resolve_traffic_sql_col_map_impl,
    _table_column_names,
)
from .constants import (
    CRASH_TIMEZONE,
    DRIVABLE_HIGHWAY_TAGS,
    NON_DRIVABLE_HINT_TERMS,
    TRAFFIC_MAP_LIMIT_DEFAULT,
    TRAFFIC_MAP_LIMIT_MAX,
    TRAFFIC_MAP_LIMIT_NEAR_MAX,
    TRAFFIC_MAP_QUERY_TIMEOUT_MS,
    TRAFFIC_NEAR_COMBINED_MAX,
    TRAFFIC_NEAR_CRASH_OVERLAY_MAX,
    TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS,
    TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX,
    TRAFFIC_RESULT_LIMIT_WITH_MAP,
    TRAFFIC_SQL_RESULT_TIMEOUT_MS,
    _CRASH_SQL_COL,
    _HB_SQL_COL,
    _SQL_COL,
)
from .filters import (
    _build_road_name_patterns,
    _collect_road_filter_scope_values,
    _compile_filter,
    _extract_near_filters,
    _has_hard_brake_filter,
    _is_road_only_filter,
    _normalize_road_value,
    _sanitize_sql_filters,
)
from .map_points import _build_map_points, _map_payload_from_points_df
from .map_payloads import (
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _build_groupby_bar_chart_payload,
    _df_to_markdown_safe,
    _make_crash_map_payload,
    _make_map_payload,
    _publish_chart_payload,
    _query_requests_chart,
)
from .query_helpers import (
    _get_column_suggestions,
    _groupby_columns_from_params,
    _has_count_aggregation,
    _normalize_metric_key,
    _normalize_sort,
    _resolve_sql_column_key,
    _safe_float8_expr,
    _schema_safe_column,
    _sort_needs_roads,
)


class QueryablePolicyError(ValueError):
    """Raised when a requested column is outside the dataset's queryable policy."""


def _load_dataset_queryable_policy(
    dataset_id: str,
    *,
    entity_type: Optional[str] = None,
    available_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    default_fields = resolve_queryable_fields(entity_type, {}, available_sources=available_sources)
    default_policy = {
        "entity_type": entity_type,
        "fields": default_fields,
        "alias_map": build_queryable_alias_map(default_fields),
        "enabled_query_names": sorted(
            {
                str(item.get("query_name", "")).strip()
                for item in default_fields
                if isinstance(item, dict) and bool(item.get("enabled", True))
            }
        ),
    }
    if not dataset_id:
        return default_policy

    uid = (get_active_user() or "dev-user").strip() or "dev-user"
    try:
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT entity_type, mapping, stats
                FROM """ + APP_DATASETS + """
                WHERE dataset_id=%s AND owner_user_id=%s
                LIMIT 1
                """,
                (dataset_id, uid),
            )
            row = cur.fetchone()
        if not row:
            return default_policy

        stats_obj = row.get("stats")
        if isinstance(stats_obj, str):
            try:
                stats_obj = json.loads(stats_obj)
            except Exception:
                stats_obj = {}

        mapping_obj = row.get("mapping")
        if isinstance(mapping_obj, str):
            try:
                mapping_obj = json.loads(mapping_obj)
            except Exception:
                mapping_obj = {}

        queryable_obj = {}
        if isinstance(stats_obj, dict):
            candidate = stats_obj.get("queryable_fields")
            if isinstance(candidate, dict):
                queryable_obj = candidate

        effective_entity = (
            str(entity_type or "").strip().lower()
            or str((mapping_obj or {}).get("entity_type") or "").strip().lower()
            or str(row.get("entity_type") or "").strip().lower()
            or "event"
        )

        fields = resolve_queryable_fields(
            effective_entity,
            queryable_obj,
            available_sources=available_sources,
        )
        return {
            "entity_type": effective_entity,
            "fields": fields,
            "alias_map": build_queryable_alias_map(fields),
            "enabled_query_names": sorted(
                {
                    str(item.get("query_name", "")).strip()
                    for item in fields
                    if isinstance(item, dict) and bool(item.get("enabled", True))
                }
            ),
        }
    except Exception:
        return default_policy


_QUERYABLE_FIELD_ALIASES: Dict[str, str] = {
    "crash_time": "accident_time",
    "time": "accident_time",
    "time_of_day": "local_time",
    "timeofday": "local_time",
    "hour": "local_hour",
    "hour_of_day": "local_hour",
}


def _resolve_queryable_source_column(raw_column: Any, alias_map: Dict[str, str]) -> Optional[str]:
    key = normalize_query_name(raw_column)
    if not key:
        return None
    key = _QUERYABLE_FIELD_ALIASES.get(key, key)
    return alias_map.get(key)


def _queryable_policy_guidance(blocked_column: Any, allowed_query_names: List[str]) -> str:
    blocked = str(blocked_column or "").strip() or "(blank)"
    allowed = [name for name in allowed_query_names if str(name or "").strip()]
    allowed_preview = ", ".join(allowed[:18]) if allowed else "(none configured)"
    if len(allowed) > 18:
        allowed_preview += f", ... (+{len(allowed) - 18} more)"
    return (
        f"Field '{blocked}' is not queryable for this dataset.\n"
        f"Allowed query fields: {allowed_preview}\n"
        "To add this field:\n"
        "1) Open Ingestion.\n"
        "2) Select this dataset.\n"
        "3) In Queryable Fields, click Add field.\n"
        "4) Enter a Query name and Source column, keep it enabled, then Save queryable fields.\n"
        "5) Retry your question using that query name."
    )


def _load_dataset_mapping_fields(dataset_id: str) -> Dict[str, str]:
    if not dataset_id:
        return {}
    uid = (get_active_user() or "dev-user").strip() or "dev-user"
    try:
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT mapping, stats
                FROM """ + APP_DATASETS + """
                WHERE dataset_id=%s AND owner_user_id=%s
                LIMIT 1
                """,
                (dataset_id, uid),
            )
            row = cur.fetchone()
        if not row:
            return {}

        mapping_obj = row.get("mapping")
        if isinstance(mapping_obj, str):
            try:
                mapping_obj = json.loads(mapping_obj)
            except Exception:
                mapping_obj = {}
        mapping_fields = mapping_obj.get("fields") if isinstance(mapping_obj, dict) else None

        if not isinstance(mapping_fields, dict) or not mapping_fields:
            stats_obj = row.get("stats")
            if isinstance(stats_obj, str):
                try:
                    stats_obj = json.loads(stats_obj)
                except Exception:
                    stats_obj = {}
            ingest_obj = stats_obj.get("ingest") if isinstance(stats_obj, dict) else {}
            mapping_fields = ingest_obj.get("mapping_fields") if isinstance(ingest_obj, dict) else {}

        if not isinstance(mapping_fields, dict):
            return {}

        sanitized: Dict[str, str] = {}
        for key, value in mapping_fields.items():
            name = str(value).strip() if isinstance(value, str) else ""
            if not name:
                continue
            if re.match(r"^[A-Za-z0-9_]+$", name):
                sanitized[str(key)] = name
        return sanitized
    except Exception:
        return {}


def _resolve_traffic_sql_col_map(
    conn,
    hard_brake_only: bool,
    alias: str = "p",
    base_table: Optional[str] = None,
) -> Dict[str, str]:
    return _resolve_traffic_sql_col_map_impl(
        conn,
        hard_brake_only,
        alias=alias,
        base_table=base_table,
        sql_col_map=_SQL_COL,
        hb_sql_col_map=_HB_SQL_COL,
    )

__all__ = [
    "Any",
    "CRASH_TIMEZONE",
    "DRIVABLE_HIGHWAY_TAGS",
    "Dict",
    "List",
    "NON_DRIVABLE_HINT_TERMS",
    "Optional",
    "QueryablePolicyError",
    "RealDictCursor",
    "TRAFFIC_MAP_LIMIT_DEFAULT",
    "TRAFFIC_MAP_LIMIT_MAX",
    "TRAFFIC_MAP_LIMIT_NEAR_MAX",
    "TRAFFIC_MAP_QUERY_TIMEOUT_MS",
    "TRAFFIC_NEAR_COMBINED_MAX",
    "TRAFFIC_NEAR_CRASH_OVERLAY_MAX",
    "TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS",
    "TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX",
    "TRAFFIC_RESULT_LIMIT_WITH_MAP",
    "TRAFFIC_SQL_RESULT_TIMEOUT_MS",
    "_CRASH_SQL_COL",
    "_active_cv_run_context",
    "_apply_codebook_labels",
    "_build_auto_chart_payload_from_df",
    "_build_groupby_bar_chart_payload",
    "_build_map_points",
    "_build_road_name_patterns",
    "_collect_road_filter_scope_values",
    "_compile_filter",
    "_cv_relation_candidates",
    "_db_conn",
    "_df_to_markdown_safe",
    "_extract_near_filters",
    "_first_existing_relation",
    "_get_column_suggestions",
    "_get_event_schema_columns",
    "_has_count_aggregation",
    "_has_hard_brake_filter",
    "_is_road_only_filter",
    "_latest_dataset_id_from_relation",
    "_latest_event_dataset_id",
    "_load_dataset_mapping_fields",
    "_load_dataset_queryable_policy",
    "_queryable_policy_guidance",
    "_resolve_queryable_source_column",
    "_lookup_pedestrian_accident_type_codes",
    "_make_crash_map_payload",
    "_make_map_payload",
    "_map_payload_from_points_df",
    "_normalize_metric_key",
    "_normalize_road_value",
    "_normalize_sort",
    "_publish_chart_payload",
    "_query_requests_chart",
    "_resolve_cv_enrichment_sql",
    "_resolve_event_dataset_id",
    "_resolve_hard_brake_table",
    "_resolve_point_coord_exprs",
    "_resolve_sql_column_key",
    "_resolve_traffic_sql_col_map",
    "_safe_float8_expr",
    "_sanitize_sql_filters",
    "_schema_safe_column",
    "_sort_needs_roads",
    "_table_column_names",
    "get_active_session",
    "json",
    "logging",
    "pd",
    "re",
    "save_map_for_session",
    "traceback",
]

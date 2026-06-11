"""Workzone SQL domain operations."""

from typing import Any, Dict, Optional

from ..services.maps import make_workzone_map_payload as _make_workzone_map_payload
from ..storage.postgis.table_names import APP_EVENTS
from .common import (
    QueryablePolicyError,
    RealDictCursor,
    _CRASH_SQL_COL,
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _compile_filter,
    _db_conn,
    _df_to_markdown_safe,
    _first_existing_relation,
    _get_column_suggestions,
    _get_event_schema_columns,
    _latest_event_dataset_id,
    _load_dataset_mapping_fields,
    _load_dataset_queryable_policy,
    _normalize_metric_key,
    _normalize_sort,
    _publish_chart_payload,
    _queryable_policy_guidance,
    _query_requests_chart,
    _resolve_queryable_source_column,
    _resolve_event_dataset_id,
    _resolve_sql_column_key,
    _safe_float8_expr,
    _schema_safe_column,
    _sort_needs_roads,
    json,
    logging,
    pd,
    re,
    save_map_for_session,
    traceback,
)

def get_workzone_domain_schema(dataset_id: Optional[str] = None) -> dict:
    """
    Returns a schema description for the workzone SQL domain so the planner knows
    which columns are valid for filtering, grouping, and aggregating.
    """
    core_columns = list(_CRASH_SQL_COL.keys())

    groupable_columns = [
        "road_name", "road", "vehicle_impact", "location_method",
        "event_date", "start_date", "end_date",
    ]

    aggregate_columns = [
        "count", "reduced_speed_limit_kph",
    ]

    resolved_id = dataset_id
    dynamic_cols: list[str] = []
    try:
        if not resolved_id:
            resolved_id = _resolve_event_dataset_id(None, entity_type="workzone") or _latest_event_dataset_id(entity_type="workzone")
        if resolved_id:
            dynamic_cols = sorted(_get_event_schema_columns(resolved_id))
    except Exception:
        pass

    policy = _load_dataset_queryable_policy(
        resolved_id or "",
        entity_type="workzone",
        available_sources=sorted(set(core_columns + dynamic_cols)),
    )
    queryable_columns = sorted(
        {
            str(item.get("query_name", "")).strip()
            for item in policy.get("fields") or []
            if isinstance(item, dict) and bool(item.get("enabled", True))
        }
    )
    all_columns = queryable_columns or sorted(set(core_columns + dynamic_cols))
    all_col_set = set(all_columns)
    groupable_columns = [col for col in groupable_columns if col in all_col_set]
    aggregate_columns = [col for col in aggregate_columns if col == "count" or col in all_col_set]

    return {
        "domain": "workzone",
        "dataset_id": resolved_id,
        "columns": all_columns,
        "groupable_columns": groupable_columns,
        "aggregate_columns": aggregate_columns,
        "allowed_aggregations": ["count", "sum", "mean", "avg", "min", "max"],
        "notes": [
            "Use 'count' aggregation to count workzone records.",
            "Key columns: vehicle_impact, location_method, start_date, end_date, reduced_speed_limit_kph.",
            "Use road_name or road to filter by road.",
            "Only queryable fields configured for this dataset are available.",
        ],
    }


def run_workzone_sql_operations(dataset: str, query: Dict[str, Any]) -> str:
    """
    SQL-backed execution for the WORKZONE dataset (PostGIS events table).
    Supports: filter, groupby, aggregate, unique_values, sort, head, generate_map.
    """
    log = logging.getLogger("adk_server")
    try:
        if dataset.lower() not in {"workzone", "workzones"}:
            raise ValueError("SQL workzone mode supports datasets: workzone/workzones.")

        dataset_id_input = query.get("dataset_id")
        dataset_id = _resolve_event_dataset_id(dataset_id_input, entity_type="workzone")
        if not dataset_id:
            dataset_id = _latest_event_dataset_id(entity_type="workzone")
        if not dataset_id:
            raise ValueError("No workzone dataset_id found for this session.")

        reasoning = query.get("reasoning", "")
        steps = query.get("steps", []) or []

        def _safe_sql_alias(raw: Any, default: str = "count") -> str:
            alias = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or "").strip().lower())
            alias = alias.strip("_")
            if not alias:
                alias = default
            if alias[0].isdigit():
                alias = f"c_{alias}"
            return alias

        def _iter_agg_specs(aggs: dict, *, mode: str) -> list[dict[str, str]]:
            specs: list[dict[str, str]] = []
            for raw_key, raw_val in (aggs or {}).items():
                agg_key = str(raw_key or "").strip() or "count"
                alias_override: Optional[str] = None
                col = agg_key
                fn = ""

                if isinstance(raw_val, dict):
                    fn = str(
                        raw_val.get("fn")
                        or raw_val.get("function")
                        or raw_val.get("agg")
                        or ""
                    ).strip().lower()
                    col = str(raw_val.get("column") or raw_val.get("col") or agg_key).strip() or agg_key
                    alias_raw = str(raw_val.get("alias") or "").strip()
                    alias_override = alias_raw or None
                else:
                    fn = str(raw_val or "").strip().lower()
                    m = re.match(r"^\s*([a-z_]+)\s*\(\s*([a-zA-Z0-9_*]+)\s*\)\s*$", fn)
                    if m:
                        fn = m.group(1).strip().lower()
                        inner_col = m.group(2).strip()
                        col = "count" if inner_col == "*" else inner_col

                if col == "*":
                    col = "count"
                if not fn:
                    raise ValueError(f"Missing aggregation fn for '{agg_key}' in SQL workzone {mode} mode")

                specs.append(
                    {
                        "key": agg_key,
                        "column": col or agg_key,
                        "fn": fn,
                        "alias": alias_override or "",
                    }
                )
            return specs

        filters: list[dict] = []
        filter_mode = "and"
        map_req: Optional[dict] = None
        groupby_req: Optional[dict] = None
        aggregate_req: Optional[dict] = None
        unique_values_req: Optional[dict] = None
        sort_req: Optional[dict] = None
        head_req: Optional[int] = None
        resolved_group_cols_for_chart: list[str] = []

        for step in steps:
            op = (step.get("operation") or "").lower()
            params = step.get("params", {}) or {}
            if op == "filter":
                filters.extend(params.get("conditions", []) or [])
                filter_mode = params.get("mode", filter_mode) or filter_mode
            elif op == "generate_map":
                map_req = params
            elif op == "groupby":
                groupby_req = params
            elif op == "aggregate":
                aggregate_req = params
            elif op == "unique_values":
                unique_values_req = params
            elif op == "sort":
                sort_req = params
            elif op == "head":
                head_req = int(params.get("n", 10))
            else:
                raise ValueError(
                    f"SQL workzone mode does not support operation '{op}' yet. "
                    "Supported: filter, generate_map, groupby, aggregate, unique_values, sort, head."
                )

        where_parts = ["e.dataset_id = %s"]
        where_params: list = [dataset_id]

        crash_col_map = dict(_CRASH_SQL_COL)
        with _db_conn() as conn:
            roads_table = _first_existing_relation(conn, ["public.roads", "roads"])
        if not roads_table:
            road_expr_no_join = (
                "COALESCE("
                "NULLIF(e.props->>'road',''), "
                "NULLIF(e.props->>'road_name',''), "
                "NULLIF(e.props->>'roadName','')"
                ")"
            )
            crash_col_map["road"] = road_expr_no_join
            crash_col_map["road_name"] = road_expr_no_join
            crash_col_map["name"] = road_expr_no_join
        schema_cols = _get_event_schema_columns(dataset_id)
        mapping_fields = _load_dataset_mapping_fields(dataset_id)
        numeric_ops = {">", ">=", "<", "<="}

        def _props_expr(source_col: Optional[str], *, numeric: bool = False) -> Optional[str]:
            col = str(source_col or "").strip()
            if not col or not re.match(r"^[A-Za-z0-9_]+$", col):
                return None
            expr = f"NULLIF(e.props->>'{col}','')"
            return _safe_float8_expr(expr) if numeric else expr

        mapped_primary_expr = _props_expr(mapping_fields.get("primary_id"))
        if mapped_primary_expr:
            crash_col_map["primary_id"] = f"COALESCE({mapped_primary_expr}, {crash_col_map['primary_id']})"

        mapped_start_expr = _props_expr(mapping_fields.get("start_time"))
        if mapped_start_expr:
            crash_col_map["start_date"] = f"COALESCE({mapped_start_expr}, {crash_col_map['start_date']})"

        mapped_end_expr = _props_expr(mapping_fields.get("end_time"))
        if mapped_end_expr:
            crash_col_map["end_date"] = f"COALESCE({mapped_end_expr}, {crash_col_map['end_date']})"

        mapped_timestamp_expr = _props_expr(mapping_fields.get("timestamp"))
        if mapped_timestamp_expr:
            crash_col_map["timestamp"] = f"COALESCE({mapped_timestamp_expr}, CAST(e.ts AS text))"

        mapped_road_id_expr = _props_expr(mapping_fields.get("road_id"))
        if mapped_road_id_expr:
            crash_col_map["road_segment_id"] = f"COALESCE(NULLIF(e.road_segment_id,''), {mapped_road_id_expr})"

        mapped_road_name_expr = _props_expr(mapping_fields.get("road_name"))
        queryable_policy = _load_dataset_queryable_policy(
            dataset_id,
            entity_type="workzone",
            available_sources=sorted(set(schema_cols) | set(crash_col_map.keys())),
        )
        queryable_alias_map = queryable_policy.get("alias_map") if isinstance(queryable_policy, dict) else {}
        if not isinstance(queryable_alias_map, dict):
            queryable_alias_map = {}
        enabled_query_names = queryable_policy.get("enabled_query_names") if isinstance(queryable_policy, dict) else []
        if not isinstance(enabled_query_names, list):
            enabled_query_names = []

        def _ensure_event_col(col: str, *, numeric: bool = False) -> None:
            if not col or col in crash_col_map:
                return
            target = _schema_safe_column(col, schema_cols)
            if not target:
                return
            if not re.match(r"^[A-Za-z0-9_]+$", target):
                return
            base_expr = f"NULLIF(e.props->>'{target}','')"
            expr = _safe_float8_expr(base_expr) if numeric else base_expr
            crash_col_map[col] = expr

        for field in queryable_policy.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if not bool(field.get("enabled", True)):
                continue
            query_name = str(field.get("query_name") or "").strip()
            source_col = str(field.get("source_column") or "").strip()
            if not query_name or not source_col:
                continue
            if source_col not in crash_col_map:
                _ensure_event_col(source_col, numeric=False)
            if source_col in crash_col_map and query_name not in crash_col_map:
                crash_col_map[query_name] = crash_col_map[source_col]

        def _require_queryable(col_name: Any, *, allow_count: bool = False) -> str:
            text = str(col_name or "").strip()
            if allow_count and _normalize_metric_key(text) == "count":
                return "count"
            source_col = _resolve_queryable_source_column(text, queryable_alias_map)
            if not source_col:
                raise QueryablePolicyError(_queryable_policy_guidance(text, enabled_query_names))
            if source_col not in crash_col_map and _schema_safe_column(source_col, schema_cols) is None:
                raise QueryablePolicyError(_queryable_policy_guidance(text, enabled_query_names))
            return source_col

        def _iter_leaf_conditions(conditions: list[dict]):
            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                nested = condition.get("conditions")
                if isinstance(nested, list):
                    yield from _iter_leaf_conditions(nested)
                else:
                    yield condition

        for cond in _iter_leaf_conditions(filters):
            col = (cond.get("column") or "").strip()
            if not col:
                continue
            source_col = _require_queryable(col)
            if source_col != col:
                cond["column"] = source_col
            col = source_col
            if col in crash_col_map:
                continue
            if not re.match(r"^[A-Za-z0-9_]+$", col):
                continue
            op = (cond.get("operator") or "").strip().lower()
            val = cond.get("value", None)
            is_numeric = op in numeric_ops
            if op == "between" and isinstance(val, (list, tuple)) and len(val) == 2:
                is_numeric = all(isinstance(v, (int, float)) for v in val)
            if isinstance(val, (int, float)):
                is_numeric = True
            _ensure_event_col(col, numeric=is_numeric)

        def _find_unknown_filter_column(conditions: list[dict]) -> Optional[str]:
            for condition in conditions or []:
                if "conditions" in condition:
                    nested = _find_unknown_filter_column(condition.get("conditions") or [])
                    if nested:
                        return nested
                    continue
                col_name = (condition.get("column") or "").strip()
                if col_name and col_name not in crash_col_map:
                    return col_name
            return None

        unknown_filter_col = _find_unknown_filter_column(filters)
        if unknown_filter_col:
            suggestions = _get_column_suggestions(unknown_filter_col, schema_cols)
            raise ValueError(f"Unsupported filter column '{unknown_filter_col}' in SQL workzone mode.{suggestions}")

        filt_clause, filt_params, needs_roads = _compile_filter(filters, filter_mode, crash_col_map)
        if filt_clause:
            where_parts.append(f"({filt_clause})")
            where_params.extend(filt_params)

        join_roads = needs_roads
        if groupby_req:
            join_roads = join_roads or any(
                (_resolve_queryable_source_column(c, queryable_alias_map) or c) in ("road", "road_name", "name")
                for c in (groupby_req.get("group_by") or [])
            )
        if unique_values_req:
            unique_col_candidate = str(
                unique_values_req.get("column")
                or unique_values_req.get("col")
                or ""
            ).strip()
            if unique_col_candidate:
                resolved_unique_join_col = _resolve_queryable_source_column(unique_col_candidate, queryable_alias_map) or _resolve_sql_column_key(unique_col_candidate, crash_col_map)
                join_roads = join_roads or (resolved_unique_join_col in ("road", "road_name", "name"))
        if sort_req:
            join_roads = join_roads or _sort_needs_roads(sort_req)
        from_sql = f"{APP_EVENTS} e"
        if join_roads and roads_table:
            from_sql += f" LEFT JOIN {roads_table} r ON r.road_segment_id = e.road_segment_id"

        where_sql = " AND ".join(where_parts)
        road_name_parts = []
        if join_roads and roads_table:
            road_name_parts.append("r.name")
        if mapped_road_name_expr:
            road_name_parts.append(mapped_road_name_expr)
        road_name_parts.extend(
            [
                "NULLIF(e.props->>'road','')",
                "NULLIF(e.props->>'road_name','')",
                "NULLIF(e.props->>'roadName','')",
            ]
        )
        road_name_expr = "COALESCE(" + ", ".join(road_name_parts) + ")"

        if map_req is not None:
            limit = int(map_req.get("limit", 5000))

            map_sql = f"""
                SELECT
                    e.id,
                    e.lat AS latitude,
                    e.lon AS longitude,
                    e.ts AS timestamp,
                    e.road_segment_id,
                    {road_name_expr} AS road_name,
                    e.props->>'geometry' AS geometry,
                    e.props->>'core_details' AS core_details,
                    {crash_col_map["start_date"]} AS start_date,
                    {crash_col_map["end_date"]} AS end_date,
                    {crash_col_map["vehicle_impact"]} AS vehicle_impact,
                    {crash_col_map["location_method"]} AS location_method,
                    {crash_col_map["reduced_speed_limit_kph"]} AS reduced_speed_limit_kph
                FROM {from_sql}
                WHERE {where_sql}
                LIMIT %s
            """

            with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(map_sql, where_params + [limit])
                rows = cur.fetchall()

            save_map_for_session(_make_workzone_map_payload(rows, label="Workzone Map", exclusive=True, dataset_id=dataset_id), map_type="workzone")

            log.info(json.dumps({
                "event": "sql_compiled_map",
                "dataset": dataset,
                "dataset_id": dataset_id,
                "sql": map_sql.strip()[:2500],
                "params": where_params + [limit]
            }, default=str))

        sort_cols, sort_dirs = _normalize_sort(sort_req)
        if unique_values_req is not None:
            requested_col = str(
                unique_values_req.get("column")
                or unique_values_req.get("col")
                or ""
            ).strip()
            if not requested_col:
                raise ValueError("unique_values requires params.column")
            requested_col = _require_queryable(requested_col)

            resolved_unique_col = _resolve_sql_column_key(requested_col, crash_col_map)
            if resolved_unique_col is None:
                _ensure_event_col(requested_col, numeric=False)
                resolved_unique_col = _resolve_sql_column_key(requested_col, crash_col_map)
            if resolved_unique_col is None:
                suggestions = _get_column_suggestions(requested_col, schema_cols)
                raise ValueError(f"Unsupported unique_values column '{requested_col}' in SQL workzone mode.{suggestions}")

            include_nulls = bool(
                unique_values_req.get("include_nulls")
                or unique_values_req.get("include_null")
            )
            limit_raw = unique_values_req.get("limit", unique_values_req.get("n", 100))
            try:
                unique_limit = int(limit_raw)
            except (TypeError, ValueError):
                unique_limit = 100
            if head_req is not None:
                unique_limit = min(unique_limit, int(head_req))
            unique_limit = max(1, min(100, unique_limit))

            raw_value_expr = f"NULLIF(TRIM(CAST({crash_col_map[resolved_unique_col]} AS text)), '')"
            grouped_value_expr = (
                f"COALESCE({raw_value_expr}, '[NULL]')" if include_nulls else raw_value_expr
            )
            value_alias = resolved_unique_col

            base_where_sql = where_sql
            base_where_params = list(where_params)
            if not include_nulls:
                base_where_sql = f"{base_where_sql} AND {raw_value_expr} IS NOT NULL"

            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    key_norm = _normalize_metric_key(col)
                    if key_norm == "count":
                        order_expr = "count"
                    elif key_norm in {"value", _normalize_metric_key(requested_col), _normalize_metric_key(value_alias)}:
                        order_expr = value_alias
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for unique_values result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"
            else:
                order_clause = f" ORDER BY count DESC, {value_alias} ASC"

            sql = f"""
                SELECT
                    {grouped_value_expr} AS {value_alias},
                    COUNT(*) AS count
                FROM {from_sql}
                WHERE {base_where_sql}
                GROUP BY {grouped_value_expr}
                {order_clause}
                LIMIT %s
            """
            where_params = base_where_params + [unique_limit]
        elif groupby_req is not None:
            raw_group_cols = groupby_req.get("group_by") or []
            group_cols = [_require_queryable(col) for col in raw_group_cols]
            resolved_group_cols_for_chart = list(group_cols)
            aggs = groupby_req.get("aggregations") or {}
            if not group_cols or not aggs:
                raise ValueError("groupby requires group_by and aggregations")

            gb_exprs = []
            gb_selects = []
            for c in group_cols:
                if c not in crash_col_map:
                    raise ValueError(f"Unsupported group_by column '{c}' in SQL workzone mode")
                gb_exprs.append(crash_col_map[c])
                gb_selects.append(f"{crash_col_map[c]} AS {c}")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_alias_map: dict[str, str] = {}
            for spec in _iter_agg_specs(aggs, mode="groupby"):
                agg_key = spec["key"]
                fn = spec["fn"]
                raw_col = spec["column"]
                if fn == "count":
                    col = _resolve_queryable_source_column(raw_col, queryable_alias_map) or "count"
                else:
                    col = _require_queryable(raw_col)
                alias_override = spec["alias"] or None
                if col not in crash_col_map and col != "count" and fn != "count":
                    raise ValueError(f"Unsupported aggregation column '{col}' in SQL workzone mode")
                if col == "count":
                    alias = alias_override or ("count" if agg_key == "count" else _safe_sql_alias(agg_key, default="count"))
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                if col not in crash_col_map and fn == "count":
                    alias = alias_override or _safe_sql_alias(agg_key or col, default="count")
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                expr = crash_col_map[col]
                if fn in ("mean", "avg"):
                    alias = alias_override or f"{col}_avg"
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "sum":
                    alias = alias_override or f"{col}_sum"
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "min":
                    alias = alias_override or f"{col}_min"
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "max":
                    alias = alias_override or f"{col}_max"
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "count":
                    alias = alias_override or f"{col}_count"
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                    agg_aliases.append(alias)
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL workzone mode")
                agg_alias_map[agg_key] = alias
                agg_alias_map[col] = alias

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    resolved_sort_col = _resolve_queryable_source_column(col, queryable_alias_map) or col
                    if resolved_sort_col in group_cols:
                        order_expr = resolved_sort_col
                    elif col in agg_aliases:
                        order_expr = col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    elif resolved_sort_col in agg_alias_map:
                        order_expr = agg_alias_map[resolved_sort_col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for groupby result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            limit_clause = ""
            if head_req is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(head_req)]

            sql = f"""
                SELECT {", ".join(gb_selects + agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                GROUP BY {", ".join(gb_exprs)}
                {order_clause}
                {limit_clause}
            """
        elif aggregate_req is not None:
            aggs = aggregate_req.get("aggregations") or {}
            if not aggs:
                raise ValueError("aggregate requires aggregations")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_alias_map: dict[str, str] = {}
            for spec in _iter_agg_specs(aggs, mode="aggregate"):
                agg_key = spec["key"]
                fn = spec["fn"]
                raw_col = spec["column"]
                if fn == "count":
                    col = _resolve_queryable_source_column(raw_col, queryable_alias_map) or "count"
                else:
                    col = _require_queryable(raw_col)
                alias_override = spec["alias"] or None
                if col not in crash_col_map and col != "count" and fn != "count":
                    raise ValueError(f"Unsupported aggregation column '{col}' in SQL workzone mode")
                if col == "count":
                    alias = alias_override or ("count" if agg_key == "count" else _safe_sql_alias(agg_key, default="count"))
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                if col not in crash_col_map and fn == "count":
                    alias = alias_override or _safe_sql_alias(agg_key or col, default="count")
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                expr = crash_col_map[col]
                if fn in ("mean", "avg"):
                    alias = alias_override or f"{col}_avg"
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "sum":
                    alias = alias_override or f"{col}_sum"
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "min":
                    alias = alias_override or f"{col}_min"
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "max":
                    alias = alias_override or f"{col}_max"
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "count":
                    alias = alias_override or f"{col}_count"
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                    agg_aliases.append(alias)
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL workzone mode")
                agg_alias_map[agg_key] = alias
                agg_alias_map[col] = alias

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    resolved_sort_col = _resolve_queryable_source_column(col, queryable_alias_map) or col
                    if col in agg_aliases:
                        order_expr = col
                    elif resolved_sort_col in agg_aliases:
                        order_expr = resolved_sort_col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    elif resolved_sort_col in agg_alias_map:
                        order_expr = agg_alias_map[resolved_sort_col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for aggregate result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            limit_clause = ""
            if head_req is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(head_req)]

            sql = f"""
                SELECT {", ".join(agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                {order_clause}
                {limit_clause}
            """
        else:
            if map_req is not None and head_req is None:
                sql = f"""
                    SELECT COUNT(*) AS count
                    FROM {from_sql}
                    WHERE {where_sql}
                """
            else:
                limit = head_req or 20
                order_clause = ""
                if sort_cols:
                    order_parts = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        source_col = _require_queryable(col)
                        if source_col not in crash_col_map:
                            _ensure_event_col(source_col, numeric=False)
                        if source_col not in crash_col_map:
                            raise ValueError(f"Unsupported sort column '{col}' in SQL workzone mode")
                        order_parts.append(f"{crash_col_map[source_col]} {'ASC' if asc else 'DESC'}")
                    order_clause = f" ORDER BY {', '.join(order_parts)}"

                select_specs: list[tuple[str, str]] = []
                seen_aliases: set[str] = set()
                for field in queryable_policy.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    if not bool(field.get("enabled", True)):
                        continue
                    alias = str(field.get("query_name") or "").strip()
                    source_col = str(field.get("source_column") or "").strip()
                    if not alias or alias in seen_aliases or not source_col:
                        continue
                    if source_col not in crash_col_map:
                        _ensure_event_col(source_col, numeric=False)
                    if source_col not in crash_col_map:
                        continue
                    select_specs.append((alias, crash_col_map[source_col]))
                    seen_aliases.add(alias)

                for _cond in _iter_leaf_conditions(filters):
                    source_col = (_cond.get("column") or "").strip()
                    if not source_col or source_col not in crash_col_map or source_col in seen_aliases:
                        continue
                    select_specs.append((source_col, crash_col_map[source_col]))
                    seen_aliases.add(source_col)

                if not select_specs:
                    raise QueryablePolicyError(
                        "No enabled queryable fields are currently available for tabular output.\n"
                        "Open Ingestion > Queryable Fields, enable at least one valid field, save, and retry."
                    )

                select_clause = ",\n                    ".join(
                    f"{expr} AS {alias}"
                    for alias, expr in select_specs
                )
                sql = f"""
                    SELECT
                    {select_clause}
                    FROM {from_sql}
                    WHERE {where_sql}
                    {order_clause}
                    LIMIT %s
                """
                where_params = where_params + [limit]

        log.info(json.dumps({
            "event": "sql_compiled",
            "dataset": dataset,
            "dataset_id": dataset_id,
            "sql": sql.strip()[:2500],
            "params": where_params
        }, default=str))

        with _db_conn() as conn:
            df = pd.read_sql_query(sql, conn, params=where_params)

        log.info(json.dumps({
            "event": "sql_result",
            "rows": int(len(df)),
            "cols": list(df.columns)[:50]
        }, default=str))

        df = _apply_codebook_labels(df, dataset_id=dataset_id)
        wants_chart = _query_requests_chart(reasoning, steps)
        chart_payload: list[dict[str, Any]] = []
        if wants_chart:
            chart_payload_single = _build_auto_chart_payload_from_df(
                df=df,
                reasoning=reasoning,
                steps=steps,
                    chart_role="workzone_query_result",
                    dataset_id=dataset_id,
                    group_cols=resolved_group_cols_for_chart if resolved_group_cols_for_chart else ((groupby_req.get("group_by") or []) if groupby_req else None),
                )
            if chart_payload_single:
                chart_payload.append(chart_payload_single)
                _publish_chart_payload(chart_payload, label="Workzone analysis chart")

        response_parts = ["REPORT FROM WORKZONE (SQL) SPECIALIST:\n"]
        if reasoning:
            response_parts.append(f"Analysis Plan: {reasoning}\n")
        response_parts.append(f"Workzone dataset_id used: {dataset_id}")
        if filters:
            response_parts.append(f"Filters applied: {filters}")
        if map_req is not None:
            response_parts.append(f"Map generated with limit={int(map_req.get('limit', 5000))}")
        if chart_payload:
            response_parts.append("Visualization generated in panel.")

        response_parts.append("\nFINAL DATA RESULTS:")
        if df.empty:
            response_parts.append("Result is an empty table (No data found).")
        elif wants_chart and chart_payload:
            response_parts.append(f"Returned {len(df)} rows for the chart.")
        else:
            response_parts.append(_df_to_markdown_safe(df))

        return "\n".join(response_parts)

    except QueryablePolicyError as e:
        msg = f"[QUERYABLE_POLICY_BLOCKED]\n{str(e)}"
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"❌ SQL MODE ERROR: {str(e)}\nTraceback:\n{traceback.format_exc()}"
        print(error_msg)
        return error_msg

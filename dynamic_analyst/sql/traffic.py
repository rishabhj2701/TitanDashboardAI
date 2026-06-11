"""Traffic SQL domain operations."""

from __future__ import annotations

from ..storage.postgis.table_names import APP_EVENTS
from ..services.cv import latest_cv_dataset_id as _latest_cv_dataset_id
from ..services.maps import make_workzone_map_payload as _make_workzone_map_payload
from ..session_state import get_last_map
from .common import (
    Any,
    CRASH_TIMEZONE,
    Dict,
    Optional,
    RealDictCursor,
    TRAFFIC_MAP_QUERY_TIMEOUT_MS,
    TRAFFIC_RESULT_LIMIT_WITH_MAP,
    TRAFFIC_SQL_RESULT_TIMEOUT_MS,
    _active_cv_run_context,
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _build_road_name_patterns,
    _collect_road_filter_scope_values,
    _compile_filter,
    _cv_relation_candidates,
    _db_conn,
    _relation_has_rows,
    _resolve_cv_base_table,
    _df_to_markdown_safe,
    _extract_near_filters,
    _first_existing_relation,
    _has_hard_brake_filter,
    _is_road_only_filter,
    _latest_event_dataset_id,
    _make_crash_map_payload,
    _make_map_payload,
    _normalize_metric_key,
    _normalize_road_value,
    _normalize_sort,
    _publish_chart_payload,
    _query_requests_chart,
    _resolve_cv_enrichment_sql,
    _resolve_event_dataset_id,
    _resolve_hard_brake_table,
    _resolve_point_coord_exprs,
    _groupby_columns_from_params,
    _resolve_sql_column_key,
    _resolve_traffic_sql_col_map,
    _sanitize_sql_filters,
    _table_column_names,
    get_active_session,
    json,
    logging,
    pd,
    save_map_for_session,
    traceback,
)
from .traffic_aggregates import (
    _append_drivable_highway_clause,
    _pick_column,
    _run_top_hard_braking_roads_operation_impl,
    _run_top_hard_braking_roads_from_road_stats,
    _resolve_road_stats_table,
    _run_roads_by_speed_limit_operation_impl,
    _run_top_speed_roads_operation_impl,
    _save_road_aggregate_filter_from_df,
    _table_has_column,
    _table_highway_uses_osm_tags,
    _try_run_road_stats_optimized,
)
from .traffic_filters import (
    _collect_road_filter_values,
    _collect_road_filters,
    _filters_request_non_drivable_roads,
    _normalize_derived_metric_references,
    _parse_agg_specs,
    _parse_traffic_sql_steps,
)
from .traffic_map import (
    _build_traffic_map_sql,
    _cap_combined_near_points,
    _fetch_near_crash_rows,
    _fetch_near_workzone_rows,
    _is_reset_map_request,
    _resolve_traffic_map_sampling,
    _traffic_map_label,
)

def get_traffic_domain_schema(dataset_id: Optional[str] = None) -> dict:
    """
    Returns a schema snapshot for the traffic SQL domain so the planner can:
      - see valid group-by columns
      - see valid aggregation columns
      - avoid guessing non-existent columns like traffic_volume
    """
    try:
        with _db_conn() as conn:
            # Traffic SQL mode often uses a road stats MV/table; resolve it dynamically.
            stats_table = _resolve_road_stats_table(conn)

            cols: list[str] = []
            if stats_table:
                cols = _table_column_names(conn, stats_table) or []

            cols_set = set(cols)

            # Heuristics: groupable fields tend to be id/label/categorical columns.
            preferred_groupable = [
                "label",
                "ref",
                "road_name",
                "name",
                "road_segment_id",
                "segment_id",
                "route_network",
                "highway",
                "county",
                "state",
                "direction",
                "dir",
            ]
            groupable_columns = [c for c in preferred_groupable if c in cols_set]

            # Heuristics: aggregatable fields tend to be numeric metrics.
            metric_tokens = (
                "count",
                "speed",
                "volume",
                "aadt",
                "vmt",
                "p85",
                "pct",
                "avg",
                "mean",
                "min",
                "max",
                "sum",
                "brake",
            )
            aggregate_columns = [
                c for c in cols
                if any(tok in c.lower() for tok in metric_tokens)
            ]
            if "hard_brake_count" in cols_set and "hard_brake_count" not in aggregate_columns:
                aggregate_columns.append("hard_brake_count")

            notes = []
            if "point_count" in cols_set:
                notes.append("Use point_count as the closest proxy for traffic volume (road-level volume is represented by point_count in road stats).")
            if "traffic_volume" not in cols_set:
                notes.append("There is no traffic_volume column in the traffic SQL domain schema; do not guess it.")
            notes.append(
                "Iowa RAMS routes use label/ref (e.g. 'I 35', 'US 30') or route_id (e.g. 'S-0035'). "
                "Filter with operator='ilike' and value='%S-0035%' or '%I 35%'. "
                "The highway column holds functional class codes (1–7), not OSM tags — do not filter highway='motorway'."
            )
            if "hard_brake_count" in cols_set:
                notes.insert(
                    0,
                    "Hard braking is available as hard_brake_count per road segment (pre-aggregated). "
                    "For 'top N roads by hard brakes': domain=traffic, groupby label (or ref), "
                    "aggregations sum on hard_brake_count, sort desc, head N. "
                    "Do NOT use highway='motorway' filters.",
                )

            return {
                "domain": "traffic",
                "dataset_id": dataset_id,
                "source_table": stats_table,
                "columns": cols,
                "groupable_columns": groupable_columns,
                "aggregate_columns": aggregate_columns,
                "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
                "notes": notes,
            }
    except Exception as e:
        return {
            "domain": "traffic",
            "dataset_id": dataset_id,
            "error": str(e),
            "columns": [],
            "groupable_columns": [],
            "aggregate_columns": [],
            "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
            "notes": ["Schema lookup failed; planner should ask a clarification question rather than guessing columns."],
        }


def get_hard_braking_domain_schema(dataset_id: Optional[str] = None) -> dict:
    """
    Returns a schema snapshot for hard-braking SQL mode.
    This domain queries the hard-brake source table directly.
    """
    try:
        with _db_conn() as conn:
            hb_table = _resolve_hard_brake_table(conn)
            stats_table = _resolve_road_stats_table(conn)
            stats_cols = (
                _table_column_names(conn, stats_table) if stats_table else set()
            )
            if not hb_table and stats_table and "hard_brake_count" in stats_cols:
                traffic_schema = get_traffic_domain_schema(dataset_id)
                traffic_schema["domain"] = "hard_braking"
                traffic_schema["source_table"] = stats_table
                extra = (
                    "Point-level hard-brake events are not loaded. "
                    "Use this schema via domain=traffic: groupby label/ref and SUM(hard_brake_count) "
                    "for top-road rankings."
                )
                traffic_schema["notes"] = [extra] + list(traffic_schema.get("notes") or [])
                return traffic_schema
            if not hb_table:
                return {
                    "domain": "hard_braking",
                    "dataset_id": dataset_id,
                    "source_table": None,
                    "columns": [],
                    "groupable_columns": [],
                    "aggregate_columns": [],
                    "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
                    "notes": [
                        "Hard-braking point table is not available. "
                        "Try domain=traffic and column hard_brake_count on cv_road_stats_mv."
                    ],
                }

            cols_raw = _table_column_names(conn, hb_table) or set()
            cols = sorted(set(cols_raw))
            cols_set = set(cols)

            preferred_groupable = [
                "road_name",
                "road",
                "name",
                "road_segment_id",
                "way_id",
                "dataset_id",
                "local_hour",
                "timestamp",
            ]
            groupable_columns = [c for c in preferred_groupable if c in cols_set]

            metric_tokens = ("count", "speed", "acc", "limit", "avg", "mean", "min", "max", "sum")
            aggregate_columns = [c for c in cols if any(tok in c.lower() for tok in metric_tokens)]

            # Expose derived/query aliases that are valid in hard-braking SQL mode.
            for synthetic in ("road_name", "road_segment_id", "speed", "SpeedLimitMPH", "speed_over_limit", "acc_x", "acc_y", "timestamp"):
                if synthetic not in groupable_columns and synthetic in {"road_name", "road_segment_id", "timestamp"}:
                    groupable_columns.append(synthetic)
                if synthetic not in aggregate_columns and synthetic in {"speed", "SpeedLimitMPH", "speed_over_limit", "acc_x", "acc_y"}:
                    aggregate_columns.append(synthetic)

            notes = [
                "This domain queries hard-brake events directly (not full CV point stream).",
                "Use count for event volume, acc_x for braking intensity, and speed_over_limit for compliance deltas.",
                "Road-name filters should use ilike patterns (e.g. '%I 70%') for best matching.",
            ]

            return {
                "domain": "hard_braking",
                "dataset_id": dataset_id,
                "source_table": hb_table,
                "columns": sorted(set(cols).union({"road_name", "road_segment_id", "speed_over_limit", "acc_x", "acc_y", "timestamp"})),
                "groupable_columns": sorted(set(groupable_columns)),
                "aggregate_columns": sorted(set(aggregate_columns)),
                "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
                "notes": notes,
            }
    except Exception as e:
        return {
            "domain": "hard_braking",
            "dataset_id": dataset_id,
            "error": str(e),
            "columns": [],
            "groupable_columns": [],
            "aggregate_columns": [],
            "allowed_aggregations": ["sum", "mean", "avg", "min", "max", "count"],
            "notes": ["Schema lookup failed; planner should ask a clarification question rather than guessing columns."],
        }


def run_traffic_sql_operations(dataset: str, query: Dict[str, Any]) -> str:
    """
    SQL-backed execution for TRAFFIC (CV) and HARD_BRAKING domains.
    Supports: filter, groupby, aggregate, unique_values, sort, head, generate_map.
    Legacy compatibility: top_speed_roads.
    """
    log = logging.getLogger("adk_server")
    try:
        dataset_key = str(dataset or "").strip().lower()
        hard_brake_dataset_mode = dataset_key in {"hard_braking", "hardbraking", "hard_brake", "hardbrake", "hb"}
        if dataset_key not in {"traffic", "cv", "hard_braking", "hardbraking", "hard_brake", "hardbrake", "hb"}:
            raise ValueError(
                "This SQL runner supports traffic/CV and hard_braking domains."
            )

        dataset_id_input = query.get("dataset_id")
        dataset_id = dataset_id_input or _latest_cv_dataset_id()

        reasoning = query.get("reasoning", "")
        steps = query.get("steps", []) or []

        parsed_steps = _parse_traffic_sql_steps(steps)
        filters: list[dict] = parsed_steps["filters"]
        filter_mode = parsed_steps["filter_mode"]
        having_conditions: list[dict] = parsed_steps["having_conditions"]
        having_mode: str = parsed_steps["having_mode"]
        map_req: Optional[dict] = parsed_steps["map_req"]
        groupby_req: Optional[dict] = parsed_steps["groupby_req"]
        aggregate_req: Optional[dict] = parsed_steps["aggregate_req"]
        unique_values_req: Optional[dict] = parsed_steps["unique_values_req"]
        sort_req: Optional[dict] = parsed_steps["sort_req"]
        head_req: Optional[int] = parsed_steps["head_req"]
        top_speed_req: Optional[dict] = parsed_steps["top_speed_req"]
        top_hard_braking_req: Optional[dict] = parsed_steps["top_hard_braking_req"]
        roads_by_speed_limit_req: Optional[dict] = parsed_steps["roads_by_speed_limit_req"]
        auto_result_limit_applied: Optional[int] = None

        _normalize_derived_metric_references(
            filters=filters,
            sort_req=sort_req,
            groupby_req=groupby_req,
        )

        filters, dropped_dynamic_filters = _sanitize_sql_filters(filters)
        having_conditions, _ = _sanitize_sql_filters(having_conditions)
        if dropped_dynamic_filters:
            log.info(
                json.dumps(
                    {
                        "event": "sql_filter_placeholders_dropped",
                        "dataset": dataset,
                        "dataset_id": dataset_id,
                        "dropped": dropped_dynamic_filters,
                    },
                    default=str,
                )
            )

        road_only_map_req = (
            map_req is not None
            and groupby_req is None
            and aggregate_req is None
            and sort_req is None
            and _is_road_only_filter(filters)
        )

        filters, near_crash, near_workzone = _extract_near_filters(filters)
        hard_brake_only = bool(query.get("hard_brake_only")) or hard_brake_dataset_mode or _has_hard_brake_filter(filters)
        include_non_drivable_requested = bool(
            query.get("include_non_drivable")
            or query.get("include_non_drivable_roads")
            or query.get("include_paths")
        )

        apply_drivable_highway_default = not (
            include_non_drivable_requested or _filters_request_non_drivable_roads(filters)
        )

        cv_points_empty = False
        with _db_conn() as conn:
            stats_table = _resolve_road_stats_table(conn)
            if stats_table:
                highway_col = _pick_column(conn, stats_table, ["highway"])
                if highway_col and not _table_highway_uses_osm_tags(conn, stats_table, highway_col):
                    apply_drivable_highway_default = False
            pts_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points"))
            cv_points_empty = bool(pts_table) and not _relation_has_rows(conn, pts_table)

        if (
            cv_points_empty
            and stats_table
            and (groupby_req or aggregate_req)
            and not hard_brake_only
            and not near_crash
            and not near_workzone
        ):
            early_sort_cols, early_sort_dirs = _normalize_sort(sort_req)
            early_stats = _try_run_road_stats_optimized(
                log=log,
                dataset=dataset,
                dataset_id=dataset_id,
                near_crash=near_crash,
                near_workzone=near_workzone,
                hard_brake_only=hard_brake_only,
                groupby_req=groupby_req,
                aggregate_req=aggregate_req,
                sort_cols=early_sort_cols,
                sort_dirs=early_sort_dirs,
                head_req=head_req,
                map_req=map_req,
                filters=filters,
                filter_mode=filter_mode,
                having_conditions=having_conditions,
                having_mode=having_mode,
                apply_drivable_highway_default=apply_drivable_highway_default,
                road_filter_name=None,
                road_filter_segment=None,
            )
            if early_stats is not None:
                return early_stats

        if road_only_map_req and not near_crash and not near_workzone and not hard_brake_only:
            exact_names_raw: list[str] = []
            search_terms_raw: list[str] = []
            _collect_road_filter_scope_values(filters, exact_names_raw, search_terms_raw)

            exact_names: list[str] = []
            exact_seen: set[str] = set()
            for raw_name in exact_names_raw:
                norm = _normalize_road_value(raw_name)
                if not norm or norm in exact_seen:
                    continue
                exact_seen.add(norm)
                exact_names.append(raw_name.strip())

            search_terms: list[str] = []
            search_seen: set[str] = set()
            for raw_term in search_terms_raw:
                norm = _normalize_road_value(raw_term)
                if not norm or norm in search_seen:
                    continue
                search_seen.add(norm)
                search_terms.append(raw_term.strip())

            sid = get_active_session()
            current_map = get_last_map(sid) if sid else None
            existing_filter = (current_map or {}).get("roadAggregateFilter") or {}
            existing_names_raw = existing_filter.get("road_names") or []
            existing_names = {
                _normalize_road_value(v)
                for v in existing_names_raw
                if isinstance(v, str) and v.strip()
            }
            requested_names = {_normalize_road_value(v) for v in exact_names if isinstance(v, str) and v.strip()}

            if existing_names and requested_names and requested_names.issubset(existing_names):
                log.info(
                    json.dumps(
                        {
                            "event": "skip_redundant_road_map_call",
                            "dataset": dataset,
                            "dataset_id": dataset_id,
                            "requested_count": len(requested_names),
                            "existing_count": len(existing_names),
                        },
                        default=str,
                    )
                )
                return (
                    "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"
                    "Map scope already set to requested roads; skipped redundant map generation.\n\n"
                    "FINAL DATA RESULTS:\n"
                    "Reused prior road selection."
                )

            road_scope_filter: dict[str, Any] = {
                "min_points": 1,
            }
            if exact_names:
                road_scope_filter["road_names"] = exact_names[:40]
                road_scope_filter["limit"] = min(len(exact_names), 40)
            elif search_terms:
                road_scope_filter["road_name"] = search_terms[0]
                road_scope_filter["limit"] = 40
            else:
                # Fall through to full SQL map path if no valid road terms were extracted.
                road_scope_filter = {}

            if road_scope_filter:
                save_map_for_session(
                    {
                        "label": "Traffic Map (road filter)",
                        "count": 0,
                        "points": [],
                        "analysis_type": "traffic",
                        "roadAggregateFilter": road_scope_filter,
                    },
                    map_type="traffic",
                )
                if exact_names:
                    scope_label = ", ".join(exact_names[:10])
                    if len(exact_names) > 10:
                        scope_label += f" (+{len(exact_names) - 10} more)"
                else:
                    scope_label = search_terms[0]
                return (
                    "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"
                    "Applied road filter and updated map scope.\n\n"
                    "FINAL DATA RESULTS:\n"
                    f"Showing traffic road network for: {scope_label}"
                )

        with _db_conn() as conn:
            hard_brake_table = _resolve_hard_brake_table(conn)
            if hard_brake_only and hard_brake_table:
                base_table = hard_brake_table
            elif hard_brake_only and hard_brake_dataset_mode:
                raise ValueError("Hard-braking source table is not available.")
            else:
                base_table = _resolve_cv_base_table(conn, hard_brake_only=hard_brake_only)
            base_cols = _table_column_names(conn, base_table)
            if not base_cols:
                raise ValueError(f"Traffic source table '{base_table}' is not available.")
            if not _relation_has_rows(conn, base_table):
                stats_fallback = _resolve_road_stats_table(conn)
                if stats_fallback and (groupby_req or aggregate_req):
                    fallback_stats = _try_run_road_stats_optimized(
                        log=log,
                        dataset=dataset,
                        dataset_id=dataset_id,
                        near_crash=near_crash,
                        near_workzone=near_workzone,
                        hard_brake_only=hard_brake_only,
                        groupby_req=groupby_req,
                        aggregate_req=aggregate_req,
                        sort_cols=[],
                        sort_dirs=[],
                        head_req=head_req,
                        map_req=map_req,
                        filters=filters,
                        filter_mode=filter_mode,
                        having_conditions=having_conditions,
                        having_mode=having_mode,
                        apply_drivable_highway_default=apply_drivable_highway_default,
                        road_filter_name=None,
                        road_filter_segment=None,
                    )
                    if fallback_stats is not None:
                        return fallback_stats
                    raise ValueError(
                        "CV point-level table is empty. Use road-level aggregates "
                        "(groupby label/ref, aggregate hard_brake_count or avg_speed_mph) instead."
                    )

            lat_expr, lon_expr = _resolve_point_coord_exprs(conn, base_table, alias="p")
            col_map = _resolve_traffic_sql_col_map(
                conn,
                hard_brake_only and bool(hard_brake_table),
                alias="p",
                base_table=base_table,
            )
            enrichment = _resolve_cv_enrichment_sql(
                conn,
                base_table=base_table,
                alias="p",
                prefer_route_ref=hard_brake_only and top_hard_braking_req is None,
            )
            legacy_roads_table = _first_existing_relation(conn, ["roads", "public.roads"])
            active_run_ctx: dict[str, Any] = _active_cv_run_context()

            # Keep default dataset scoping aligned with selected CV run when possible.
            if "dataset_id" in base_cols and not dataset_id_input:
                active_run_id = active_run_ctx.get("run_id")
                if active_run_id:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"SELECT 1 FROM {base_table} WHERE dataset_id = %s LIMIT 1",
                                (active_run_id,),
                            )
                            if cur.fetchone():
                                dataset_id = str(active_run_id)
                    except Exception:
                        pass

        col_map["lat"] = lat_expr
        col_map["latitude"] = lat_expr
        col_map["lon"] = lon_expr
        col_map["longitude"] = lon_expr
        col_map["way_id"] = enrichment["way_id_expr"]
        col_map["road_segment_id"] = enrichment["road_id_expr"]
        col_map["road"] = enrichment["road_name_expr"]
        col_map["road_name"] = enrichment["road_name_expr"]
        col_map["name"] = enrichment["road_name_expr"]
        col_map["road_ref"] = enrichment.get("road_ref_expr", "NULL::text")
        col_map["highway"] = enrichment["road_name_expr"]   # LLM alias
        col_map["label"]   = enrichment["road_name_expr"]   # LLM alias
        if enrichment["speed_limit_expr"] != "NULL::float8":
            col_map["speedLimit"] = enrichment["speed_limit_expr"]
            col_map["SpeedLimitMPH"] = enrichment["speed_limit_expr"]
        col_map["speed_over_limit"] = f"(({col_map['speed']}) - ({col_map['SpeedLimitMPH']}))"
        # Common aggregate alias variants that the model may emit in follow-up filters/sorts.
        col_map["avg_speed_over_limit"] = col_map["speed_over_limit"]
        col_map["speed_over_limit_avg"] = col_map["speed_over_limit"]
        col_map["speed_over_limit_mean"] = col_map["speed_over_limit"]
        col_map["mean_speed_over_limit"] = col_map["speed_over_limit"]

        road_filter: dict = {"road_name": None, "road_segment_id": None}
        _collect_road_filters(filters, road_filter)
        road_filter_name = road_filter.get("road_name")
        road_filter_segment = road_filter.get("road_segment_id")
        where_parts: list[str] = []
        where_params: list = []
        if "dataset_id" in base_cols and dataset_id:
            where_parts.append("p.dataset_id = %s")
            where_params.append(dataset_id)

        filt_clause, filt_params, needs_roads = _compile_filter(filters, filter_mode, col_map)
        if filt_clause:
            where_parts.append(f"({filt_clause})")
            where_params.extend(filt_params)

        if near_crash:
            crash_ds = _resolve_event_dataset_id(near_crash.get("dataset_id"), entity_type="crash") or _latest_event_dataset_id(entity_type="crash")
            if not crash_ds:
                raise ValueError("No crash dataset_id found for near_crash filter.")
            where_parts.append(
                f"""
                EXISTS (
                  WITH crash_raw AS (
                    SELECT
                      e.lat AS crash_lat,
                      e.lon AS crash_lon,
                      COALESCE(
                        NULLIF(e.props->>'_event_date_norm',''),
                        NULLIF(e.props->>'accident_date',''),
                        NULLIF(e.props->>'event_date',''),
                        NULLIF(e.props->>'crash_date','')
                      ) AS crash_date_text,
                      COALESCE(
                        NULLIF(e.props->>'_event_time_norm',''),
                        NULLIF(e.props->>'accident_time',''),
                        NULLIF(e.props->>'event_time',''),
                        NULLIF(e.props->>'crash_time','')
                      ) AS crash_time_text,
                      e.ts AS raw_ts
                    FROM {APP_EVENTS} e
                    WHERE e.dataset_id = %s
                  ),
                  crash AS (
                    SELECT
                      crash_lat,
                      crash_lon,
                      CASE
                        WHEN crash_date_text IS NOT NULL
                             AND (
                               NULLIF(SUBSTRING(crash_time_text FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL
                               OR NULLIF(SUBSTRING(crash_time_text FROM '([0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL
                             )
                          THEN (
                            crash_date_text::date
                            + COALESCE(
                                NULLIF(SUBSTRING(crash_time_text FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '')::time,
                                NULLIF(SUBSTRING(crash_time_text FROM '([0-9]{{2}}:[0-9]{{2}})'), '')::time
                              )
                          ) AT TIME ZONE '{CRASH_TIMEZONE}'
                        WHEN crash_time_text ~ 'T' THEN crash_time_text::timestamptz
                        WHEN raw_ts IS NOT NULL THEN raw_ts
                        ELSE NULL
                      END AS crash_ts
                    FROM crash_raw
                  )
                  SELECT 1
                  FROM crash c
                  WHERE c.crash_ts IS NOT NULL
                    AND p.ts IS NOT NULL
                    AND p.ts BETWEEN c.crash_ts - (%s || ' minutes')::interval
                                AND c.crash_ts + (%s || ' minutes')::interval
                    AND c.crash_lat IS NOT NULL
                    AND c.crash_lon IS NOT NULL
                    AND ST_DWithin(
                      ST_SetSRID(ST_MakePoint({lon_expr}, {lat_expr}), 4326)::geography,
                      ST_SetSRID(ST_MakePoint(c.crash_lon, c.crash_lat), 4326)::geography,
                      %s
                    )
                )
                """
            )
            where_params.extend([
                crash_ds,
                near_crash.get("window_minutes", 60),
                near_crash.get("window_minutes", 60),
                near_crash.get("distance_m", 200.0),
            ])

        if near_workzone:
            workzone_ds = _resolve_event_dataset_id(near_workzone.get("dataset_id"), entity_type="workzone") or _latest_event_dataset_id(entity_type="workzone")
            if not workzone_ds:
                raise ValueError("No workzone dataset_id found for near_workzone filter.")
            where_parts.append(
                f"""
                EXISTS (
                  SELECT 1
                  FROM {APP_EVENTS} e
                  WHERE e.dataset_id = %s
                    AND p.ts IS NOT NULL
                    AND NULLIF(e.props->>'start_date','') IS NOT NULL
                    AND NULLIF(e.props->>'end_date','') IS NOT NULL
                    AND p.ts BETWEEN (e.props->>'start_date')::timestamptz AND (e.props->>'end_date')::timestamptz
                    AND e.lat IS NOT NULL
                    AND e.lon IS NOT NULL
                    AND ST_DWithin(
                      ST_SetSRID(ST_MakePoint({lon_expr}, {lat_expr}), 4326)::geography,
                      ST_SetSRID(ST_MakePoint(e.lon, e.lat), 4326)::geography,
                      %s
                    )
                )
                """
            )
            where_params.extend([
                workzone_ds,
                near_workzone.get("distance_m", 200.0),
            ])

        from_sql = f"{base_table} p"
        if enrichment["joins"]:
            from_sql += " " + " ".join(enrichment["joins"])
        if needs_roads and legacy_roads_table:
            from_sql += f" LEFT JOIN {legacy_roads_table} r ON r.road_segment_id = {col_map['road_segment_id']}"

        where_sql = " AND ".join(where_parts) if where_parts else "TRUE"

        if roads_by_speed_limit_req is not None:
            return _run_roads_by_speed_limit_operation_impl(
                roads_by_speed_limit_req,
                log=log,
                dataset_id=dataset_id,
                reasoning=reasoning,
            )

        if top_speed_req is not None:
            return _run_top_speed_roads_operation_impl(
                top_speed_req,
                log=log,
                dataset=dataset,
                dataset_id=dataset_id,
                filters=filters,
                near_crash=near_crash,
                near_workzone=near_workzone,
                hard_brake_only=hard_brake_only,
                apply_drivable_highway_default=apply_drivable_highway_default,
                col_map=col_map,
                from_sql=from_sql,
                where_sql=where_sql,
                where_params=where_params,
                lat_expr=lat_expr,
                lon_expr=lon_expr,
                reasoning=reasoning,
                active_run_ctx=active_run_ctx,
            )

        if top_hard_braking_req is not None:
            stats_ranking = _run_top_hard_braking_roads_from_road_stats(
                top_hard_braking_req,
                log=log,
                dataset=dataset,
                dataset_id=dataset_id,
                filters=filters,
                filter_mode=filter_mode,
                apply_drivable_highway_default=apply_drivable_highway_default,
            )
            if stats_ranking is not None:
                return stats_ranking
            if not hard_brake_only:
                raise ValueError(
                    "top_hard_braking_roads needs either a hard-brake point table or road-level "
                    "hard_brake_count on cv_road_stats_mv. Use domain=traffic with groupby label "
                    "and sum(hard_brake_count)."
                )
            return _run_top_hard_braking_roads_operation_impl(
                top_hard_braking_req,
                dataset_id=dataset_id,
                col_map=col_map,
                from_sql=from_sql,
                where_sql=where_sql,
                where_params=where_params,
                lat_expr=lat_expr,
                lon_expr=lon_expr,
                reasoning=reasoning,
            )

        # 1) MAP SIDE-EFFECT
        # Support mixed analytical queries like:
        #   filter -> generate_map -> aggregate/count
        # so users can see map + summary in one turn.
        if map_req is not None:
            # Lightweight reset path: clear scoped map filters without running a full CV map query.
            # This avoids expensive all-points scans when users ask to "reset map".
            if _is_reset_map_request(
                reasoning=reasoning,
                filters=filters,
                near_crash=near_crash,
                near_workzone=near_workzone,
                hard_brake_only=hard_brake_only,
            ):
                save_map_for_session(
                    {
                        "label": "Traffic Map (reset)",
                        "count": 0,
                        "points": [],
                        "analysis_type": "traffic",
                    },
                    map_type="traffic",
                )
                return (
                    "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"
                    "Map scope reset to default traffic view.\n\n"
                    "FINAL DATA RESULTS:\n"
                    "Cleared active road/map filter."
                )

            if not filt_clause.strip() and not near_crash and not near_workzone:
                return json.dumps({
                    "error": "map_requires_filter",
                    "message": (
                        "The traffic dataset is too large to display without a filter. "
                        "Please narrow by road name, date range, speed, or another column."
                    ),
                })

            _skip_raw_map_scan = False
            if (groupby_req is not None or aggregate_req is not None) and not near_crash and not near_workzone and not hard_brake_only:
                try:
                    with _db_conn() as _pf_conn:
                        if _resolve_road_stats_table(_pf_conn) is not None:
                            _skip_raw_map_scan = True
                except Exception:
                    pass  # pre-flight failed; fall back to raw scan

            if not _skip_raw_map_scan:
                limit, grid_deg, per_cell = _resolve_traffic_map_sampling(
                    map_req,
                    has_near_filter=bool(near_crash or near_workzone),
                )
                map_sql = _build_traffic_map_sql(
                    from_sql=from_sql,
                    where_sql=where_sql,
                    lat_expr=lat_expr,
                    lon_expr=lon_expr,
                    way_id_expr=enrichment["way_id_expr"],
                    road_segment_id_expr=col_map["road_segment_id"],
                    road_name_expr=col_map["road_name"],
                    speed_expr=col_map["speed"],
                    speed_limit_expr=col_map["SpeedLimitMPH"],
                    speed_over_limit_expr=col_map["speed_over_limit"],
                    acc_x_expr=col_map["acc_x"],
                    acc_y_expr=col_map["acc_y"],
                    id_expr=col_map["id"],
                    vehicle_id_expr=col_map["vehicle_id"],
                )
                map_params = [grid_deg, grid_deg] + where_params + [per_cell, limit]

                with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_MAP_QUERY_TIMEOUT_MS}ms",))
                    cur.execute(map_sql, map_params)
                    rows = cur.fetchall()

                map_payload = _make_map_payload(
                    rows,
                    label=_traffic_map_label(
                        hard_brake_only=hard_brake_only,
                        near_crash=near_crash,
                        near_workzone=near_workzone,
                    ),
                    hard_brake_only=hard_brake_only,
                )

                # If near filters are active, include matching crashes/workzones on the map
                if near_crash:
                    crash_ds = _resolve_event_dataset_id(near_crash.get("dataset_id"), entity_type="crash") or _latest_event_dataset_id(entity_type="crash")
                    if crash_ds:
                        crash_rows = _fetch_near_crash_rows(
                            from_sql=from_sql,
                            where_sql=where_sql,
                            lat_expr=lat_expr,
                            lon_expr=lon_expr,
                            where_params=where_params,
                            near_crash=near_crash,
                            crash_dataset_id=crash_ds,
                            map_limit=limit,
                        )
                        crash_payload = _make_crash_map_payload(crash_rows, label="Crashes near hard braking")
                        map_payload["points"].extend(crash_payload.get("points", []))
                        map_payload["count"] = len(map_payload["points"])

                if near_workzone:
                    workzone_ds = _resolve_event_dataset_id(near_workzone.get("dataset_id"), entity_type="workzone") or _latest_event_dataset_id(entity_type="workzone")
                    if workzone_ds:
                        wz_rows = _fetch_near_workzone_rows(
                            from_sql=from_sql,
                            where_sql=where_sql,
                            lat_expr=lat_expr,
                            lon_expr=lon_expr,
                            where_params=where_params,
                            near_workzone=near_workzone,
                            workzone_dataset_id=workzone_ds,
                            map_limit=limit,
                        )
                        wz_payload = _make_workzone_map_payload(
                            wz_rows,
                            label="Workzones near hard braking",
                            exclusive=False,
                            dataset_id=workzone_ds,
                        )
                        map_payload["lines"] = wz_payload.get("lines", [])

                if near_crash or near_workzone:
                    _cap_combined_near_points(map_payload, map_limit=limit)

                save_map_for_session(map_payload)

                log.info(json.dumps({
                    "event": "sql_compiled_map",
                    "dataset": dataset,
                    "dataset_id": dataset_id,
                    "sql": map_sql.strip()[:2500],
                    "params": map_params
                }, default=str))

        # 2) FINAL TABLE RESULT
        sort_cols, sort_dirs = _normalize_sort(sort_req)

        optimized_road_stats_response = _try_run_road_stats_optimized(
            log=log,
            dataset=dataset,
            dataset_id=dataset_id,
            near_crash=near_crash,
            near_workzone=near_workzone,
            hard_brake_only=hard_brake_only,
            groupby_req=groupby_req,
            aggregate_req=aggregate_req,
            sort_cols=sort_cols,
            sort_dirs=sort_dirs,
            head_req=head_req,
            map_req=map_req,
            filters=filters,
            filter_mode=filter_mode,
            having_conditions=having_conditions,
            having_mode=having_mode,
            col_map=col_map,
            reasoning=reasoning,
            steps=steps,
            apply_drivable_highway_default=apply_drivable_highway_default,
            road_filter_name=road_filter_name,
            road_filter_segment=road_filter_segment,
        )
        if optimized_road_stats_response is not None:
            return optimized_road_stats_response

        if unique_values_req is not None:
            requested_col = str(
                unique_values_req.get("column")
                or unique_values_req.get("col")
                or ""
            ).strip()
            if not requested_col:
                raise ValueError("unique_values requires params.column")

            resolved_unique_col = _resolve_sql_column_key(requested_col, col_map)
            if resolved_unique_col is None:
                raise ValueError(
                    f"Unsupported unique_values column '{requested_col}' in SQL mode. "
                    "Use a valid dataset column name."
                )

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

            raw_value_expr = f"NULLIF(TRIM(CAST({col_map[resolved_unique_col]} AS text)), '')"
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
            group_cols = _groupby_columns_from_params(groupby_req)
            aggs = groupby_req.get("aggregations") or {}
            if not group_cols or not aggs:
                raise ValueError("groupby requires group_by and aggregations")

            # Fast path: use pre-aggregated road stats if available (top-N by avg speed)
            if (
                not filters
                and not near_crash
                and not near_workzone
                and not hard_brake_only
                and (group_cols == ["road"] or group_cols == ["road_name"] or group_cols == ["name"])
            ):
                agg_specs, agg_alias_map = _parse_agg_specs(aggs, default_col_map=col_map)
                road_stats_compatible = all(
                    (col in ("speed", "speed_over_limit") and fn in ("mean", "avg")) or (col == "count" and fn == "count")
                    for col, fn, _ in agg_specs
                )
                if road_stats_compatible:
                    with _db_conn() as conn:
                        table = _resolve_road_stats_table(conn)
                        if table:
                            road_col = _pick_column(conn, table, ["label", "road_name", "road", "name", "ref", "highway"])
                            speed_col = _pick_column(conn, table, ["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
                            points_col = _pick_column(conn, table, ["point_count", "points", "count"])
                            speed_limit_col = _pick_column(conn, table, ["speed_limit_mph", "avg_speed_limit_mph", "speed_limit_mode"])
                            highway_col = _pick_column(conn, table, ["highway"])
                            needs_speed_limit = any(col == "speed_over_limit" for col, _, _ in agg_specs)
                            if road_col and speed_col and points_col and (not needs_speed_limit or speed_limit_col):
                                limit_clause = ""
                                params: list[Any] = []
                                where_parts_stats = [f"{road_col} IS NOT NULL", f"{speed_col} IS NOT NULL"]
                                if dataset_id and _table_has_column(conn, table, "dataset_id"):
                                    where_parts_stats.append("dataset_id = %s")
                                    params.append(dataset_id)
                                _append_drivable_highway_clause(
                                    where_parts_stats,
                                    params,
                                    highway_col,
                                    apply_drivable_highway_default=apply_drivable_highway_default,
                                    conn=conn,
                                    stats_table=table,
                                    highway_col=highway_col,
                                )

                                agg_selects: list[str] = []
                                agg_aliases: list[str] = []
                                agg_expr_map_rs: dict[str, str] = {}  # alias -> SQL expr for HAVING
                                for col, fn, alias in agg_specs:
                                    if col == "count" and fn == "count":
                                        sql_expr = f"SUM({points_col})"
                                        agg_selects.append(f"{sql_expr} AS {alias}")
                                        agg_aliases.append(alias)
                                        agg_expr_map_rs[alias] = sql_expr
                                    elif col == "speed" and fn in ("mean", "avg"):
                                        sql_expr = f"(SUM({speed_col} * {points_col}) / NULLIF(SUM({points_col}), 0))"
                                        agg_selects.append(f"{sql_expr} AS {alias}")
                                        agg_aliases.append(alias)
                                        agg_expr_map_rs[alias] = sql_expr
                                    elif col == "speed_over_limit" and fn in ("mean", "avg") and speed_limit_col:
                                        sql_expr = f"((SUM({speed_col} * {points_col}) - SUM({speed_limit_col} * {points_col})) / NULLIF(SUM({points_col}), 0))"
                                        agg_selects.append(f"{sql_expr} AS {alias}")
                                        agg_aliases.append(alias)
                                        agg_expr_map_rs[alias] = sql_expr

                                order_clause = ""
                                if sort_cols:
                                    order_parts = []
                                    for col, asc in zip(sort_cols, sort_dirs):
                                        if col in agg_aliases:
                                            order_expr = col
                                        elif col in agg_alias_map:
                                            order_expr = agg_alias_map[col]
                                        else:
                                            continue
                                        order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                                    if order_parts:
                                        order_clause = f" ORDER BY {', '.join(order_parts)}"

                                having_clause_rs = ""
                                if having_conditions:
                                    hav_col_map_rs = dict(agg_expr_map_rs)
                                    for k, v in agg_alias_map.items():
                                        if v in agg_expr_map_rs:
                                            hav_col_map_rs[k] = agg_expr_map_rs[v]
                                    try:
                                        hav_clause, hav_params, _ = _compile_filter(
                                            having_conditions, having_mode, hav_col_map_rs
                                        )
                                        if hav_clause:
                                            having_clause_rs = f" HAVING {hav_clause}"
                                            params.extend(hav_params)
                                    except Exception:
                                        pass  # Skip HAVING; full SQL path will handle it.

                                effective_head = head_req
                                if effective_head is None and map_req is not None:
                                    effective_head = TRAFFIC_RESULT_LIMIT_WITH_MAP
                                    auto_result_limit_applied = effective_head
                                if effective_head is not None:
                                    limit_clause = " LIMIT %s"
                                    params.append(int(effective_head))

                                sql = f"""
                                    SELECT
                                        {road_col} AS road,
                                        {", ".join(agg_selects)}
                                    FROM {table}
                                    WHERE {" AND ".join(where_parts_stats)}
                                    GROUP BY {road_col}
                                    {having_clause_rs}
                                    {order_clause}
                                    {limit_clause}
                                """

                                log.info(json.dumps({
                                    "event": "sql_compiled_road_stats",
                                    "dataset": dataset,
                                    "dataset_id": dataset_id,
                                    "sql": sql.strip()[:2500],
                                    "params": params
                                }, default=str))

                                df = pd.read_sql_query(sql, conn, params=params)
                                df = _apply_codebook_labels(df, dataset_id=dataset_id)
                                _save_road_aggregate_filter_from_df(
                                    df,
                                    log=log,
                                    dataset=dataset,
                                    dataset_id=dataset_id,
                                    road_filter_name=road_filter_name,
                                    road_filter_segment=road_filter_segment,
                                    head_req=head_req,
                                    group_cols_in=group_cols,
                                )
                                response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
                                if reasoning:
                                    response_parts.append(f"Analysis Plan: {reasoning}\n")
                                response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
                                response_parts.append("Using pre-aggregated road statistics")
                                if _query_requests_chart(reasoning, steps):
                                    auto_chart = _build_auto_chart_payload_from_df(
                                        df=df,
                                        reasoning=reasoning,
                                        steps=steps,
                                        chart_role="traffic_query_result",
                                        dataset_id=dataset_id or "__all__",
                                        group_cols=group_cols,
                                    )
                                    if auto_chart:
                                        _publish_chart_payload([auto_chart], label="Traffic analysis chart")
                                        response_parts.append("Visualization generated in panel.")

                                response_parts.append("\nFINAL DATA RESULTS:")
                                if df.empty:
                                    response_parts.append("Result is an empty table (No data found).")
                                else:
                                    response_parts.append(_df_to_markdown_safe(df))
                                return "\n".join(response_parts)

            gb_exprs = []
            gb_selects = []
            for c in group_cols:
                if c not in col_map:
                    raise ValueError(f"Unsupported group_by column '{c}' in SQL mode")
                gb_exprs.append(col_map[c])
                gb_selects.append(f"{col_map[c]} AS {c}")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_specs, agg_alias_map = _parse_agg_specs(aggs, default_col_map=col_map)
            for col, fn, alias in agg_specs:
                if col == "count":
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    continue
                expr = col_map[col]
                if fn in ("mean", "avg"):
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                elif fn == "sum":
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                elif fn == "min":
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                elif fn == "max":
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                elif fn == "count":
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL mode")
                agg_aliases.append(alias)

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    if col in group_cols:
                        order_expr = col
                    elif col in agg_aliases:
                        order_expr = col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for groupby result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            # Build HAVING clause from post-groupby filter conditions.
            having_clause = ""
            if having_conditions:
                having_col_map = {alias: alias for alias in agg_aliases}
                having_col_map.update({v: v for v in agg_alias_map.values()})
                for k, v in agg_alias_map.items():
                    having_col_map[k] = v
                hav_clause, hav_params, _ = _compile_filter(having_conditions, having_mode, having_col_map)
                if hav_clause:
                    having_clause = f" HAVING {hav_clause}"
                    where_params = where_params + hav_params

            limit_clause = ""
            effective_head = head_req
            if effective_head is None and map_req is not None:
                effective_head = TRAFFIC_RESULT_LIMIT_WITH_MAP
                auto_result_limit_applied = effective_head
            if effective_head is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(effective_head)]

            sql = f"""
                SELECT {", ".join(gb_selects + agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                GROUP BY {", ".join(gb_exprs)}
                {having_clause}
                {order_clause}
                {limit_clause}
            """
        elif aggregate_req is not None:
            aggs = aggregate_req.get("aggregations") or {}
            if not aggs:
                raise ValueError("aggregate requires aggregations")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_specs, agg_alias_map = _parse_agg_specs(aggs, default_col_map=col_map)

            # Fast path: road counts using pre-aggregated road stats
            road_filters: list[tuple[str, Any]] = []
            _collect_road_filter_values(filters, road_filters)
            if road_filters:
                has_negative = any(op in ("!=", "not in") for op, _ in road_filters)
            else:
                has_negative = False

            count_only = all(col == "count" and fn == "count" for col, fn, _ in agg_specs)
            if count_only and road_filters and not has_negative and not near_crash and not near_workzone and not hard_brake_only:
                with _db_conn() as conn:
                    table = _resolve_road_stats_table(conn)
                    if table:
                        road_col = _pick_column(conn, table, ["label", "road_name", "road", "name", "ref", "highway"])
                        points_col = _pick_column(conn, table, ["point_count", "points", "count"])
                        highway_col = _pick_column(conn, table, ["highway"])
                        if road_col and points_col:
                            exact_vals: list[str] = []
                            patterns: list[str] = []
                            for op, val in road_filters:
                                if val is None:
                                    continue
                                if op in ("==", "="):
                                    exact_vals.append(str(val))
                                elif op == "in" and isinstance(val, (list, tuple)):
                                    exact_vals.extend([str(v) for v in val if v is not None])
                                elif op in ("contains", "ilike", "like"):
                                    patterns.extend(_build_road_name_patterns(str(val)))
                                elif op == "startswith":
                                    patterns.append(f"{str(val)}%")
                                elif op == "endswith":
                                    patterns.append(f"%{str(val)}")
                                else:
                                    patterns.extend(_build_road_name_patterns(str(val)))

                            where_parts_stats = [f"{road_col} IS NOT NULL"]
                            params: list[Any] = []
                            if dataset_id and _table_has_column(conn, table, "dataset_id"):
                                where_parts_stats.append("dataset_id = %s")
                                params.append(dataset_id)
                            _append_drivable_highway_clause(
                                where_parts_stats,
                                params,
                                highway_col,
                                apply_drivable_highway_default=apply_drivable_highway_default,
                                conn=conn,
                                stats_table=table,
                                highway_col=highway_col,
                            )

                            if exact_vals:
                                placeholders = ",".join(["%s"] * len(exact_vals))
                                where_parts_stats.append(f"{road_col} IN ({placeholders})")
                                params.extend(exact_vals)

                            if patterns:
                                where_parts_stats.append("(" + " OR ".join([f"{road_col} ILIKE %s"] * len(patterns)) + ")")
                                params.extend(patterns)

                            limit_clause = ""
                            if head_req is not None:
                                limit_clause = " LIMIT %s"
                                params.append(int(head_req))

                            sql = f"""
                                SELECT
                                    {road_col} AS road,
                                    SUM({points_col}) AS count
                                FROM {table}
                                WHERE {" AND ".join(where_parts_stats)}
                                GROUP BY {road_col}
                                ORDER BY count DESC
                                {limit_clause}
                            """

                            log.info(json.dumps({
                                "event": "sql_compiled_road_stats_count",
                                "dataset": dataset,
                                "dataset_id": dataset_id,
                                "sql": sql.strip()[:2500],
                                "params": params
                            }, default=str))

                            df = pd.read_sql_query(sql, conn, params=params)
                            df = _apply_codebook_labels(df, dataset_id=dataset_id)
                            response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
                            if reasoning:
                                response_parts.append(f"Analysis Plan: {reasoning}\n")
                            response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
                            response_parts.append("Using pre-aggregated road statistics")
                            if _query_requests_chart(reasoning, steps):
                                auto_chart = _build_auto_chart_payload_from_df(
                                    df=df,
                                    reasoning=reasoning,
                                    steps=steps,
                                    chart_role="traffic_query_result",
                                    dataset_id=dataset_id or "__all__",
                                    group_cols=["road"],
                                )
                                if auto_chart:
                                    _publish_chart_payload([auto_chart], label="Traffic analysis chart")
                                    response_parts.append("Visualization generated in panel.")

                            response_parts.append("\nFINAL DATA RESULTS:")
                            if df.empty:
                                response_parts.append("Result is an empty table (No data found).")
                            else:
                                response_parts.append(_df_to_markdown_safe(df))
                            return "\n".join(response_parts)

            for col, fn, alias in agg_specs:
                if col == "count":
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    continue
                expr = col_map[col]
                if fn in ("mean", "avg"):
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                elif fn == "sum":
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                elif fn == "min":
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                elif fn == "max":
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                elif fn == "count":
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL mode")
                agg_aliases.append(alias)

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    if col in agg_aliases:
                        order_expr = col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for aggregate result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            limit_clause = ""
            effective_head = head_req
            if effective_head is None and map_req is not None:
                effective_head = TRAFFIC_RESULT_LIMIT_WITH_MAP
                auto_result_limit_applied = effective_head
            if effective_head is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(effective_head)]

            sql = f"""
                SELECT {", ".join(agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                {order_clause}
                {limit_clause}
            """
        else:
            # If the user asked for a map but didn't explicitly ask for a table,
            # don't return a 20-row preview (it confuses UI + wastes tokens).
            if map_req is not None and head_req is None:
                sql = f"""
                    SELECT COUNT(*) AS count
                    FROM {from_sql}
                    WHERE {where_sql}
                """
                # where_params unchanged
            else:
                limit = head_req or 20
                order_clause = ""
                if sort_cols:
                    order_parts = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        if col not in col_map:
                            raise ValueError(f"Unsupported sort column '{col}' in SQL mode")
                        order_parts.append(f"{col_map[col]} {'ASC' if asc else 'DESC'}")
                    order_clause = f" ORDER BY {', '.join(order_parts)}"
                sql = f"""
                    SELECT
                    p.ts AS timestamp,
                    {lat_expr} AS latitude,
                    {lon_expr} AS longitude,
                    {enrichment["way_id_expr"]} AS way_id,
                    {col_map["road_segment_id"]} AS road_segment_id,
                    {col_map["road_name"]} AS road_name,
                    {col_map["speed"]} AS speed,
                    {col_map["SpeedLimitMPH"]} AS "speedLimit",
                    {col_map["speed_over_limit"]} AS speed_over_limit
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
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_SQL_RESULT_TIMEOUT_MS}ms",))
            df = pd.read_sql_query(sql, conn, params=where_params)
        df = _apply_codebook_labels(df, dataset_id=dataset_id)
        if groupby_req is not None:
            group_cols = _groupby_columns_from_params(groupby_req)
            if any(c in ("road", "road_name", "name") for c in group_cols):
                _save_road_aggregate_filter_from_df(
                    df,
                    log=log,
                    dataset=dataset,
                    dataset_id=dataset_id,
                    road_filter_name=road_filter_name,
                    road_filter_segment=road_filter_segment,
                    head_req=head_req,
                    group_cols_in=group_cols,
                )

        log.info(json.dumps({
            "event": "sql_result",
            "rows": int(len(df)),
            "cols": list(df.columns)[:50]
        }, default=str))

        wants_chart = _query_requests_chart(reasoning, steps)
        chart_generated = False

        response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
        if reasoning:
            response_parts.append(f"Analysis Plan: {reasoning}\n")
        response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
        if filters:
            response_parts.append(f"Filters applied: {filters}")
        if near_crash:
            response_parts.append(
                "Near-crash filter: "
                f"distance_m={near_crash.get('distance_m', 200.0)}, "
                f"window_minutes={near_crash.get('window_minutes', 60)}"
            )
        if near_workzone:
            response_parts.append(
                "Near-workzone filter: "
                f"distance_m={near_workzone.get('distance_m', 200.0)}"
            )
        if map_req is not None:
            response_parts.append(f"Map generated with limit={int(map_req.get('limit', 5000))}")
        if auto_result_limit_applied is not None:
            response_parts.append(
                f"Result rows auto-limited to top {auto_result_limit_applied} for interactive map response speed."
            )
        if wants_chart:
            auto_chart = _build_auto_chart_payload_from_df(
                df=df,
                reasoning=reasoning,
                steps=steps,
                chart_role="traffic_query_result",
                dataset_id=dataset_id or "__all__",
                group_cols=(groupby_req.get("group_by") or []) if groupby_req else None,
            )
            if auto_chart:
                _publish_chart_payload([auto_chart], label="Traffic analysis chart")
                response_parts.append("Visualization generated in panel.")
                chart_generated = True

        response_parts.append("\nFINAL DATA RESULTS:")
        if df.empty:
            response_parts.append("Result is an empty table (No data found).")
        elif wants_chart and chart_generated:
            response_parts.append(f"Returned {len(df)} rows for the chart.")
        else:
            response_parts.append(_df_to_markdown_safe(df))

        return "\n".join(response_parts)

    except Exception as e:
        error_msg = f"❌ SQL MODE ERROR: {str(e)}\nTraceback:\n{traceback.format_exc()}"
        print(error_msg)
        return error_msg

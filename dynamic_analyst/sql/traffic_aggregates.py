"""Traffic SQL aggregate/optimizer helpers."""

from __future__ import annotations

from .traffic_filters import _iter_filter_conditions, _parse_agg_specs
from .common import (
    Any,
    DRIVABLE_HIGHWAY_TAGS,
    List,
    Optional,
    RealDictCursor,
    TRAFFIC_MAP_LIMIT_MAX,
    TRAFFIC_RESULT_LIMIT_WITH_MAP,
    TRAFFIC_SQL_RESULT_TIMEOUT_MS,
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _compile_filter,
    _cv_relation_candidates,
    _db_conn,
    _df_to_markdown_safe,
    _first_existing_relation,
    _make_map_payload,
    _publish_chart_payload,
    _query_requests_chart,
    _table_column_names,
    json,
    pd,
    save_map_for_session,
)

def _table_has_column(conn, table_name: str, column: str) -> bool:
    cols = _table_column_names(conn, table_name) or set()
    return str(column).lower() in cols


def _stats_table_supports_required_columns(cols: set[str], required_columns: set[str]) -> bool:
    alias_support = {
        "road_name": {"road_name", "name", "label", "road"},
        "road": {"road_name", "name", "label", "road"},
        "name": {"road_name", "name", "label", "road"},
        "label": {"road_name", "name", "label", "road"},
        "road_segment_id": {"road_segment_id", "way_id", "road_id", "segment_id"},
        "way_id": {"road_segment_id", "way_id", "road_id", "segment_id"},
        "road_id": {"road_segment_id", "way_id", "road_id", "segment_id"},
        "ref": {"ref", "route", "highway_ref", "highwayref", "highway"},
        "route": {"ref", "route", "highway_ref", "highwayref", "highway"},
        "road_ref": {"ref", "route", "highway_ref", "highwayref", "highway"},
        "highway_ref": {"ref", "route", "highway_ref", "highwayref", "highway"},
    }
    for col in required_columns:
        if col == "count":
            continue
        if col in cols:
            continue
        aliases = alias_support.get(col)
        if aliases and aliases.intersection(cols):
            continue
        return False
    return True


def _resolve_road_stats_table(conn, required_columns: Optional[set[str]] = None) -> Optional[str]:
    candidates: list[str] = []
    candidates.extend(_cv_relation_candidates(conn, "cv_road_stats_mv"))
    candidates.extend(_cv_relation_candidates(conn, "cv_road_segment_stats"))
    candidates.extend(_cv_relation_candidates(conn, "cv_road_agg"))
    candidates.extend(
        [
            "viz_matched_roads_tbl",
            "public.viz_matched_roads_tbl",
            "mm_rawmatch.cv_road_segment_stats",
            "mm_rawmatch.cv_road_agg",
        ]
    )
    candidates = list(dict.fromkeys(candidates))
    selected: Optional[str] = None
    with conn.cursor() as cur:
        for name in candidates:
            cur.execute("SELECT to_regclass(%s)", (name,))
            row = cur.fetchone()
            if not row or not row[0]:
                continue
            if selected is None:
                selected = name
            if required_columns:
                cols = _table_column_names(conn, name) or set()
                if _stats_table_supports_required_columns(cols, required_columns):
                    return name
            else:
                return name
    return selected


def _resolve_route_segment_stats_table(conn) -> Optional[str]:
    candidates: list[str] = []
    candidates.extend(_cv_relation_candidates(conn, "cv_route_segment_stats"))
    candidates.extend(["public.cv_route_segment_stats", "cv_route_segment_stats"])
    return _first_existing_relation(conn, list(dict.fromkeys(candidates)))


def _route_segment_filter_col_map(cols: set[str]) -> dict[str, str]:
    speed_expr = "speed_mean_mph::float8" if "speed_mean_mph" in cols else "NULL::float8"
    ts_expr = "timestamp_5min" if "timestamp_5min" in cols else "NULL::timestamptz"
    hour_expr = "hour::int" if "hour" in cols else "NULL::int"
    col_map = {
        "route_id": "route_id::text",
        "road_segment_id": "route_id::text",
        "way_id": "route_id::text",
        "road": "route_id::text",
        "road_name": "route_id::text",
        "name": "route_id::text",
        "ref": "route_id::text",
        "road_ref": "route_id::text",
        "routeid": "route_id::text",
        "speed": speed_expr,
        "avg_speed": speed_expr,
        "avg_speed_mph": speed_expr,
        "speed_mean_mph": speed_expr,
        "SpeedMPH": speed_expr,
        "decel_03g_sum": "COALESCE(decel_03g_sum, 0)",
        "hard_brake_count": "COALESCE(decel_03g_sum, 0)",
        "timestamp": ts_expr,
        "timestamp_5min": ts_expr,
        "start_ts": ts_expr,
        "ts_start": ts_expr,
        "end_ts": ts_expr,
        "hour": hour_expr,
        "local_hour": hour_expr,
    }
    if "year" in cols:
        col_map["year"] = "year::int"
    if "month" in cols:
        col_map["month"] = "month::int"
    if "day" in cols:
        col_map["day"] = "day::int"
    return col_map


def _has_route_speed_filter(filters: list[dict]) -> bool:
    speed_cols = {"speed", "avgspeed", "meanspeed", "speedmeanmph", "avgspeedmph", "speedmph"}
    for cond in _iter_filter_conditions(filters):
        col_norm = "".join(ch for ch in str(cond.get("column") or "").lower() if ch.isalnum())
        if col_norm in speed_cols:
            return True
    return False


def _try_run_route_segment_speed_filter_operation(
    *,
    dataset_id: Optional[str],
    filters: list[dict],
    filter_mode: str,
    head_req: Optional[int],
    reasoning: str,
) -> Optional[str]:
    if not filters or not _has_route_speed_filter(filters):
        return None

    limit_n = _bounded_int(head_req, default=25, minimum=1, maximum=200)
    try:
        with _db_conn() as conn:
            table = _resolve_route_segment_stats_table(conn)
            cols = _table_column_names(conn, table) if table else set()
            if not table or not {"route_id", "speed_mean_mph"}.issubset(cols):
                return None

            col_map = _route_segment_filter_col_map(cols)
            where_clause, params, _ = _compile_filter(filters, filter_mode, col_map)
            if not where_clause:
                return None

            sql = f"""
                WITH matched_bins AS (
                    SELECT
                        route_id::text AS route_id,
                        speed_mean_mph::float8 AS speed_mean_mph,
                        COALESCE(decel_03g_sum, 0)::bigint AS decel_03g_sum
                    FROM {table}
                    WHERE {where_clause}
                )
                SELECT
                    route_id,
                    COUNT(*)::bigint AS matching_bins,
                    AVG(speed_mean_mph)::float8 AS avg_matching_speed_mph,
                    MIN(speed_mean_mph)::float8 AS min_matching_speed_mph,
                    MAX(speed_mean_mph)::float8 AS max_matching_speed_mph,
                    SUM(decel_03g_sum)::bigint AS hard_brake_count
                FROM matched_bins
                GROUP BY route_id
                ORDER BY avg_matching_speed_mph ASC NULLS LAST, matching_bins DESC, route_id ASC
                LIMIT %s
            """
            df = pd.read_sql_query(sql, conn, params=list(params) + [limit_n])
            df = _apply_codebook_labels(df, dataset_id=dataset_id)
    except Exception:
        return None

    segment_ids = [
        str(raw).strip()
        for raw in df.get("route_id", pd.Series(dtype=object)).tolist()
        if str(raw or "").strip()
    ]
    if segment_ids:
        map_payload = _make_map_payload(
            [],
            label=f"Route speed ranking ({len(segment_ids)} routes)",
            hard_brake_only=True,
        )
        map_payload["roadAggregateFilter"] = {
            "road_segment_ids": segment_ids[:40],
            "road_segment_id": segment_ids[0],
            "limit": min(len(segment_ids), 40),
            "group_by": "route_id",
            "metric": "avg_matching_speed_mph",
        }
        map_payload["analysis_type"] = "route_speed_ranking"
        map_payload["overlay"] = False
        save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    response_parts.append("Source: uploaded Iowa CV route segment aggregate table.")
    response_parts.append("Filter metric: speed_mean_mph on 5-minute route-segment bins.")
    if segment_ids:
        response_parts.append(f"Map output: highlighted {len(segment_ids)} ranked routes on the map.")
    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no route bins matched the speed filter).")
    else:
        response_parts.append(_df_to_markdown_safe(df))
    return "\n".join(response_parts)


def _try_run_highlight_route_ids_operation(
    params: dict,
    *,
    reasoning: str,
) -> Optional[str]:
    raw_ids = params.get("road_segment_ids")
    ids: list[str] = []
    if isinstance(raw_ids, list):
        ids = [str(v).strip() for v in raw_ids if str(v or "").strip()]
    single = str(params.get("road_segment_id") or "").strip()
    if single and single not in ids:
        ids.insert(0, single)
    ids = ids[:40]
    if not ids:
        return None

    map_payload = _make_map_payload(
        [],
        label=f"Route highlight ({ids[0]})",
        hard_brake_only=True,
    )
    map_payload["roadAggregateFilter"] = {
        "road_segment_ids": ids,
        "road_segment_id": ids[0],
        "limit": len(ids),
        "group_by": "route_id",
    }
    map_payload["analysis_type"] = "route_highlight"
    map_payload["overlay"] = False
    save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"Map output: highlighted route(s): {', '.join(ids[:10])}")
    response_parts.append("\nFINAL DATA RESULTS:\n")
    response_parts.append(f"Center map on RAMS route ID **{ids[0]}** (if geometry exists in cv_road_stats_mv).")
    return "\n".join(response_parts)


def _try_run_lowest_avg_speed_routes_operation(
    params: dict,
    *,
    dataset_id: Optional[str],
    reasoning: str,
) -> Optional[str]:
    """Rank routes by mean speed (cv_route_segment_stats) and highlight on map."""
    limit_n = _bounded_int(params.get("limit"), default=10, minimum=1, maximum=50)
    min_avg = float(params.get("min_avg_speed_mph") or 0.5)
    min_avg = max(0.0, min(120.0, min_avg))
    try:
        with _db_conn() as conn:
            table = _resolve_route_segment_stats_table(conn)
            cols = _table_column_names(conn, table) if table else set()
            if not table or not {"route_id", "speed_mean_mph"}.issubset(cols):
                return None
            sql = f"""
                SELECT
                    route_id::text AS route_id,
                    COUNT(*)::bigint AS matching_bins,
                    AVG(speed_mean_mph)::float8 AS avg_speed_mph,
                    MIN(speed_mean_mph)::float8 AS min_speed_mph,
                    MAX(speed_mean_mph)::float8 AS max_speed_mph
                FROM {table}
                WHERE route_id IS NOT NULL
                  AND speed_mean_mph IS NOT NULL
                GROUP BY route_id
                HAVING AVG(speed_mean_mph) > %s
                ORDER BY avg_speed_mph ASC NULLS LAST, matching_bins DESC, route_id ASC
                LIMIT %s
            """
            df = pd.read_sql_query(sql, conn, params=[min_avg, limit_n])
            df = _apply_codebook_labels(df, dataset_id=dataset_id)
    except Exception:
        return None

    segment_ids = [
        str(raw).strip()
        for raw in df.get("route_id", pd.Series(dtype=object)).tolist()
        if str(raw or "").strip()
    ]
    if segment_ids:
        map_payload = _make_map_payload(
            [],
            label=f"Lowest {len(segment_ids)} routes by average speed",
            hard_brake_only=True,
        )
        map_payload["roadAggregateFilter"] = {
            "road_segment_ids": segment_ids[:40],
            "road_segment_id": segment_ids[0],
            "limit": min(len(segment_ids), 40),
            "group_by": "route_id",
            "metric": "avg_speed_mph",
        }
        map_payload["analysis_type"] = "lowest_avg_speed_routes"
        map_payload["overlay"] = False
        save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    response_parts.append(
        f"Source: {table} — routes ranked by AVG(speed_mean_mph), excluding averages <= {min_avg} mph."
    )
    if segment_ids:
        response_parts.append(f"Map output: highlighted {len(segment_ids)} slowest routes.")
    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no routes matched).")
    else:
        response_parts.append(_df_to_markdown_safe(df))
    return "\n".join(response_parts)


def _try_run_top_hard_braking_route_segments_operation(
    params: dict,
    *,
    dataset_id: Optional[str],
    reasoning: str,
) -> Optional[str]:
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    try:
        with _db_conn() as conn:
            route_stats_table = _resolve_route_segment_stats_table(conn)
            route_stats_cols = _table_column_names(conn, route_stats_table) if route_stats_table else set()
            if not route_stats_table or not {"route_id", "decel_03g_sum"}.issubset(route_stats_cols):
                return None

            speed_expr = (
                "AVG(speed_mean_mph)::float8"
                if "speed_mean_mph" in route_stats_cols
                else "NULL::float8"
            )
            min_accel_expr = (
                "MIN(acceleration_min)::float8"
                if "acceleration_min" in route_stats_cols
                else "NULL::float8"
            )
            start_expr = (
                "MIN(timestamp_5min)"
                if "timestamp_5min" in route_stats_cols
                else "NULL::timestamptz"
            )
            end_expr = (
                "MAX(timestamp_5min)"
                if "timestamp_5min" in route_stats_cols
                else "NULL::timestamptz"
            )
            rank_sql = f"""
                WITH route_stats AS (
                    SELECT
                        route_id::text AS road_segment_id,
                        route_id::text AS road_ref,
                        route_id::text AS road_name,
                        SUM(COALESCE(decel_03g_sum, 0))::bigint AS hard_brake_count,
                        {min_accel_expr} AS min_acc_x,
                        {speed_expr} AS avg_speed_mph,
                        {start_expr} AS start_ts,
                        {end_expr} AS end_ts
                    FROM {route_stats_table}
                    WHERE route_id IS NOT NULL
                    GROUP BY route_id
                    HAVING SUM(COALESCE(decel_03g_sum, 0)) > 0
                )
                SELECT
                    ROW_NUMBER() OVER (ORDER BY hard_brake_count DESC, road_name ASC) AS rank,
                    road_name,
                    road_segment_id,
                    road_ref,
                    hard_brake_count,
                    NULL::float8 AS avg_acc_x,
                    min_acc_x,
                    avg_speed_mph,
                    NULL::float8 AS avg_speed_over_limit_mph,
                    start_ts,
                    end_ts
                FROM route_stats
                ORDER BY rank
                LIMIT %s
            """
            df = pd.read_sql_query(rank_sql, conn, params=[limit_n])
            df = _apply_codebook_labels(df, dataset_id=dataset_id)
    except Exception:
        return None

    top_segment_ids = [
        str(raw).strip()
        for raw in df.get("road_segment_id", pd.Series(dtype=object)).tolist()
        if str(raw or "").strip()
    ]
    if top_segment_ids:
        map_payload = _make_map_payload(
            [],
            label=f"Top {len(top_segment_ids)} routes by deceleration sum",
            hard_brake_only=True,
        )
        map_payload["roadAggregateFilter"] = {
            "road_segment_ids": top_segment_ids[:40],
            "road_segment_id": top_segment_ids[0],
            "limit": min(len(top_segment_ids), 40),
            "group_by": "route_id",
            "metric": "decel_03g_sum",
        }
        map_payload["analysis_type"] = "top_hard_braking_roads"
        map_payload["overlay"] = False
        save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    response_parts.append("Source: uploaded Iowa CV route segment aggregate table.")
    response_parts.append("Ranking metric: SUM(decel_03g_sum), descending.")
    if top_segment_ids:
        response_parts.append(f"Map output: highlighted {len(top_segment_ids)} ranked routes.")

    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no deceleration sums found).")
    else:
        response_parts.append(
            f"Top {min(limit_n, len(df))} routes by deceleration sum (hard brakes):"
        )
        for _, row in df.head(limit_n).iterrows():
            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
            route_id = str(row.get("road_name") or "Unknown route").strip()
            event_count = int(row.get("hard_brake_count")) if pd.notna(row.get("hard_brake_count")) else 0
            if rank is None:
                response_parts.append(f"- {route_id}: {event_count:,}")
            else:
                response_parts.append(f"{rank}. {route_id}: {event_count:,}")
    return "\n".join(response_parts)


def _pick_column(conn, table_name: str, options: list[str]) -> Optional[str]:
    cols = _table_column_names(conn, table_name) or set()
    for opt in options:
        if opt in cols:
            return opt
    return None



def _append_drivable_highway_clause(
    where_parts: List[str],
    params: List[Any],
    highway_expr: Optional[str],
    *,
    apply_drivable_highway_default: bool,
) -> None:
    if not apply_drivable_highway_default or not highway_expr:
        return
    where_parts.append(f"LOWER(COALESCE({highway_expr}::text, '')) = ANY(%s)")
    params.append(list(DRIVABLE_HIGHWAY_TAGS))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_top_hard_brake_group_by(value: Any) -> str:
    group_by = str(value or "segment").strip().lower()
    if group_by in {"segment", "road_segment", "road_segment_id", "way", "way_id"}:
        return "segment"
    if group_by in {"road", "road_name", "name"}:
        return "road_name"
    if group_by in {"ref", "route", "road_ref", "highway_ref"}:
        return "ref"
    return "segment"


def _save_road_aggregate_filter_from_df(
    df_in: pd.DataFrame,
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    road_filter_name: Optional[str],
    road_filter_segment: Optional[str],
    head_req: Optional[int],
    group_cols_in: Optional[list[str]] = None,
) -> None:
    try:
        if df_in is None or df_in.empty:
            return

        candidate_cols: list[str] = []
        for c in (group_cols_in or []):
            if isinstance(c, str) and c:
                candidate_cols.append(c)
        candidate_cols.extend(["road", "road_name", "name", "label", "ref", "highway"])

        available_cols: list[str] = []
        for c in candidate_cols:
            if c in df_in.columns and c not in available_cols:
                available_cols.append(c)

        road_names: list[str] = []
        for col in available_cols:
            for raw in df_in[col].tolist():
                if raw is None:
                    continue
                value = str(raw).strip()
                if not value:
                    continue
                if value.lower() in {"[unknown]", "[no_name]", "unknown", "none", "nan", "null"}:
                    continue
                road_names.append(value)

        deduped_names: list[str] = []
        seen: set[str] = set()
        for name in road_names:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_names.append(name)
            if len(deduped_names) >= 40:
                break

        if not deduped_names and not road_filter_name and not road_filter_segment:
            return

        min_points_for_map = 140
        if deduped_names:
            min_points_for_map = 1
            point_count_col = next(
                (c for c in ("point_count", "count", "points") if c in df_in.columns),
                None,
            )
            if point_count_col:
                try:
                    vals = pd.to_numeric(df_in[point_count_col], errors="coerce").dropna()
                    if not vals.empty:
                        min_points_for_map = max(1, int(vals.min()))
                except Exception:
                    min_points_for_map = 1

        payload_filter: dict[str, Any] = {
            "road_name": road_filter_name,
            "road_segment_id": road_filter_segment,
            "min_points": min_points_for_map,
        }
        if deduped_names:
            payload_filter["road_names"] = deduped_names
            payload_filter["limit"] = min(len(deduped_names), 40)
        elif head_req is not None:
            payload_filter["limit"] = int(head_req)

        save_map_for_session(
            {
                "label": "Traffic Road Aggregate",
                "roadAggregateFilter": payload_filter,
            },
            map_type="traffic",
        )
        log.info(
            json.dumps(
                {
                    "event": "road_aggregate_map_filter",
                    "dataset": dataset,
                    "dataset_id": dataset_id,
                    "filter": payload_filter,
                },
                default=str,
            )
        )
    except Exception as exc:
        log.debug("road aggregate map filter save skipped: %s", exc)


def _try_run_road_stats_optimized(
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
    hard_brake_only: bool,
    groupby_req: Optional[dict],
    aggregate_req: Optional[dict],
    sort_cols: list[str],
    sort_dirs: list[bool],
    head_req: Optional[int],
    map_req: Optional[dict],
    filters: list[dict],
    filter_mode: str,
    having_conditions: Optional[list] = None,
    having_mode: str = "and",
    col_map: dict[str, str] = None,
    reasoning: str = "",
    steps: list[dict] = None,
    apply_drivable_highway_default: bool = True,
    road_filter_name: Optional[str] = None,
    road_filter_segment: Optional[str] = None,
) -> Optional[str]:
    # Road-stats optimization is for road-level analytics only.
    if near_crash or near_workzone or hard_brake_only:
        return None
    if groupby_req is None and aggregate_req is None:
        return None

    try:
        with _db_conn() as conn:
            required_columns: set[str] = set()
            if groupby_req is not None:
                required_columns.update([c for c in (groupby_req.get("group_by") or []) if isinstance(c, str)])
            if aggregate_req is not None:
                required_columns.update(
                    [c for c in (aggregate_req.get("aggregations") or {}).keys() if isinstance(c, str) and c != "count"]
                )
            if filters:
                for cond in _iter_filter_conditions(filters):
                    col = cond.get("column")
                    if isinstance(col, str):
                        required_columns.add(col)
            if having_conditions:
                for cond in _iter_filter_conditions(having_conditions):
                    col = cond.get("column")
                    if isinstance(col, str):
                        required_columns.add(col)

            # Call resolver without extra kwargs to preserve monkeypatch compatibility.
            table = _resolve_road_stats_table(conn)
            if table and required_columns:
                cols = _table_column_names(conn, table) or set()
                if not _stats_table_supports_required_columns(cols, required_columns):
                    return None
            if not table:
                return None

            road_col = _pick_column(conn, table, ["label", "road_name", "road", "name", "ref", "highway"])
            road_id_col = _pick_column(conn, table, ["road_segment_id", "way_id", "segment_id", "road_id"])
            points_col = _pick_column(conn, table, ["point_count", "points", "count"])
            speed_col = _pick_column(conn, table, ["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
            speed_limit_col = _pick_column(conn, table, ["speed_limit_mph", "avg_speed_limit_mph", "speed_limit_mode"])
            min_speed_col = _pick_column(conn, table, ["min_speed_mph", "min_speed"])
            max_speed_col = _pick_column(conn, table, ["max_speed_mph", "max_speed"])
            std_speed_col = _pick_column(conn, table, ["speed_stddev_mph", "stddev_speed_mph", "speed_stddev"])
            start_ts_col = _pick_column(conn, table, ["start_ts", "ts_start", "min_ts"])
            end_ts_col = _pick_column(conn, table, ["end_ts", "ts_end", "max_ts"])
            ref_col = _pick_column(conn, table, ["ref"])
            highway_col = _pick_column(conn, table, ["highway"])

            if not road_col or not points_col:
                return None

            road_expr = f"NULLIF({road_col}::text,'')"
            rs_col_map: dict[str, str] = {
                "road": road_expr,
                "road_name": road_expr,
                "name": road_expr,
                "point_count": f"{points_col}::float8",
                "points": f"{points_col}::float8",
            }
            if road_id_col:
                rs_col_map["road_segment_id"] = f"{road_id_col}::text"
                rs_col_map["way_id"] = f"{road_id_col}::text"
            if speed_col:
                rs_col_map["speed"] = f"{speed_col}::float8"
                rs_col_map["avg_speed_mph"] = f"{speed_col}::float8"
                rs_col_map["avg_speed"] = f"{speed_col}::float8"
            if speed_limit_col:
                rs_col_map["SpeedLimitMPH"] = f"{speed_limit_col}::float8"
                rs_col_map["speedLimit"] = f"{speed_limit_col}::float8"
                rs_col_map["avg_speed_limit_mph"] = f"{speed_limit_col}::float8"
            else:
                rs_col_map["SpeedLimitMPH"] = "NULL::float8"
                rs_col_map["speedLimit"] = "NULL::float8"
                rs_col_map["avg_speed_limit_mph"] = "NULL::float8"
            rs_col_map["speed_over_limit"] = f"(({rs_col_map.get('speed', 'NULL::float8')}) - ({rs_col_map['SpeedLimitMPH']}))"
            rs_col_map["avg_speed_over_limit"] = rs_col_map["speed_over_limit"]
            rs_col_map["speed_over_limit_avg"] = rs_col_map["speed_over_limit"]
            rs_col_map["speed_over_limit_mean"] = rs_col_map["speed_over_limit"]
            rs_col_map["mean_speed_over_limit"] = rs_col_map["speed_over_limit"]
            if min_speed_col:
                rs_col_map["min_speed_mph"] = f"{min_speed_col}::float8"
                rs_col_map["min_speed"] = f"{min_speed_col}::float8"
            if max_speed_col:
                rs_col_map["max_speed_mph"] = f"{max_speed_col}::float8"
                rs_col_map["max_speed"] = f"{max_speed_col}::float8"
            if std_speed_col:
                rs_col_map["speed_stddev_mph"] = f"{std_speed_col}::float8"
                rs_col_map["speed_stddev"] = f"{std_speed_col}::float8"
            if start_ts_col:
                rs_col_map["start_ts"] = start_ts_col
                rs_col_map["ts_start"] = start_ts_col
            if end_ts_col:
                rs_col_map["end_ts"] = end_ts_col
                rs_col_map["ts_end"] = end_ts_col
            if ref_col:
                rs_col_map["ref"] = f"NULLIF({ref_col}::text,'')"
            if highway_col:
                rs_col_map["highway"] = f"NULLIF({highway_col}::text,'')"

            # Expose any remaining stats table columns so new CV aggregate metrics
            # (e.g. decel_03g_sum, decel_maxg_sum, hard_brake_count) can be used
            # directly in groupby/aggregate queries.
            for col in _table_column_names(conn, table):
                if col not in rs_col_map:
                    rs_col_map[col] = col
            if table and "cv_route_segment_stats" in str(table):
                for key, expr in _route_segment_filter_col_map(cols).items():
                    rs_col_map.setdefault(key, expr)

            where_parts_stats: list[str] = []
            params: list[Any] = []
            if dataset_id and _table_has_column(conn, table, "dataset_id"):
                where_parts_stats.append("dataset_id = %s")
                params.append(dataset_id)
            _append_drivable_highway_clause(
                where_parts_stats,
                params,
                highway_col,
                apply_drivable_highway_default=apply_drivable_highway_default,
            )

            try:
                filt_clause, filt_params, _ = _compile_filter(filters, filter_mode, rs_col_map)
            except Exception:
                return None
            if filt_clause:
                where_parts_stats.append(f"({filt_clause})")
                params.extend(filt_params)

            where_stats = " AND ".join(where_parts_stats) if where_parts_stats else "TRUE"

            sql = ""
            group_cols_local: list[str] = []
            if groupby_req is not None:
                group_cols_local = groupby_req.get("group_by") or []
                aggs = groupby_req.get("aggregations") or {}
                if not group_cols_local or not aggs:
                    return None

                gb_exprs: list[str] = []
                gb_selects: list[str] = []
                for c in group_cols_local:
                    if c not in rs_col_map:
                        return None
                    gb_exprs.append(rs_col_map[c])
                    gb_selects.append(f"{rs_col_map[c]} AS {c}")

                try:
                    agg_specs, agg_alias_map = _parse_agg_specs(
                        aggs,
                        default_col_map=col_map,
                        allowed_col_map=rs_col_map,
                    )
                except Exception:
                    return None
                agg_selects: list[str] = []
                agg_aliases: list[str] = []
                agg_expr_map: dict[str, str] = {}  # alias -> SQL aggregate expression (for HAVING)
                for col, fn, alias in agg_specs:
                    if col == "count":
                        sql_expr = f"SUM({points_col})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                        agg_aliases.append(alias)
                        agg_expr_map[alias] = sql_expr
                        continue
                    expr = rs_col_map[col]
                    if fn in ("mean", "avg"):
                        # Speed and speed_over_limit must be weighted by point_count to avoid
                        # treating short low-volume segments equally with high-traffic roads.
                        if col in ("speed", "speed_over_limit") and points_col:
                            speed_raw = rs_col_map.get("speed", f"{speed_col}::float8")
                            if col == "speed_over_limit" and speed_limit_col:
                                limit_raw = rs_col_map.get("SpeedLimitMPH", f"{speed_limit_col}::float8")
                                sql_expr = (
                                    f"((SUM({speed_raw} * {points_col}) - SUM({limit_raw} * {points_col}))"
                                    f" / NULLIF(SUM({points_col}), 0))"
                                )
                            else:
                                sql_expr = f"(SUM({speed_raw} * {points_col}) / NULLIF(SUM({points_col}), 0))"
                        else:
                            sql_expr = f"AVG({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "sum":
                        sql_expr = f"SUM({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "min":
                        sql_expr = f"MIN({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "max":
                        sql_expr = f"MAX({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "count":
                        if col in ("point_count", "points"):
                            sql_expr = f"SUM({points_col})"
                        else:
                            sql_expr = f"COUNT({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    else:
                        return None
                    agg_aliases.append(alias)
                    agg_expr_map[alias] = sql_expr

                order_clause = ""
                if sort_cols:
                    order_parts: list[str] = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        if col in group_cols_local:
                            order_expr = col
                        elif col in agg_aliases:
                            order_expr = col
                        elif col in agg_alias_map:
                            order_expr = agg_alias_map[col]
                        else:
                            return None
                        order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                    if order_parts:
                        order_clause = f" ORDER BY {', '.join(order_parts)}"

                # Build HAVING clause using SQL expressions (not aliases) so PostgreSQL can evaluate them.
                having_clause = ""
                if having_conditions:
                    # Map alias -> actual SQL aggregate expression for use in HAVING
                    having_col_map = dict(agg_expr_map)
                    for k, v in agg_alias_map.items():
                        if v in agg_expr_map:
                            having_col_map[k] = agg_expr_map[v]
                    try:
                        hav_clause, hav_params, _ = _compile_filter(
                            having_conditions, having_mode or "and", having_col_map
                        )
                        if hav_clause:
                            having_clause = f" HAVING {hav_clause}"
                            params.extend(hav_params)
                    except Exception:
                        return None  # Fall through to the full SQL path.

                limit_clause = ""
                effective_head = head_req
                if effective_head is None and map_req is not None:
                    effective_head = TRAFFIC_RESULT_LIMIT_WITH_MAP
                if effective_head is not None:
                    limit_clause = " LIMIT %s"
                    params.append(int(effective_head))

                sql = f"""
                    SELECT {", ".join(gb_selects + agg_selects)}
                    FROM {table}
                    WHERE {where_stats}
                    GROUP BY {", ".join(gb_exprs)}
                    {having_clause}
                    {order_clause}
                    {limit_clause}
                """
            elif aggregate_req is not None:
                aggs = aggregate_req.get("aggregations") or {}
                if not aggs:
                    return None
                try:
                    agg_specs, agg_alias_map = _parse_agg_specs(
                        aggs,
                        default_col_map=col_map,
                        allowed_col_map=rs_col_map,
                    )
                except Exception:
                    return None
                agg_selects: list[str] = []
                agg_aliases: list[str] = []
                for col, fn, alias in agg_specs:
                    if col == "count":
                        agg_selects.append(f"SUM({points_col}) AS {alias}")
                        agg_aliases.append(alias)
                        continue
                    expr = rs_col_map[col]
                    if fn in ("mean", "avg"):
                        agg_selects.append(f"AVG({expr}) AS {alias}")
                    elif fn == "sum":
                        agg_selects.append(f"SUM({expr}) AS {alias}")
                    elif fn == "min":
                        agg_selects.append(f"MIN({expr}) AS {alias}")
                    elif fn == "max":
                        agg_selects.append(f"MAX({expr}) AS {alias}")
                    elif fn == "count":
                        if col in ("point_count", "points"):
                            agg_selects.append(f"SUM({points_col}) AS {alias}")
                        else:
                            agg_selects.append(f"COUNT({expr}) AS {alias}")
                    else:
                        return None
                    agg_aliases.append(alias)

                order_clause = ""
                if sort_cols:
                    order_parts: list[str] = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        if col in agg_aliases:
                            order_expr = col
                        elif col in agg_alias_map:
                            order_expr = agg_alias_map[col]
                        else:
                            return None
                        order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                    if order_parts:
                        order_clause = f" ORDER BY {', '.join(order_parts)}"

                limit_clause = ""
                if head_req is not None:
                    limit_clause = " LIMIT %s"
                    params.append(int(head_req))

                sql = f"""
                    SELECT {", ".join(agg_selects)}
                    FROM {table}
                    WHERE {where_stats}
                    {order_clause}
                    {limit_clause}
                """
            else:
                return None

            log.info(json.dumps({
                "event": "sql_compiled_road_stats_optimized",
                "dataset": dataset,
                "dataset_id": dataset_id,
                "sql": sql.strip()[:2500],
                "params": params,
            }, default=str))

            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_SQL_RESULT_TIMEOUT_MS}ms",))
            df = pd.read_sql_query(sql, conn, params=params)

        df = _apply_codebook_labels(df, dataset_id=dataset_id)
        if groupby_req is not None:
            if any(c in ("road", "road_name", "name") for c in group_cols_local):
                _save_road_aggregate_filter_from_df(
                    df,
                    log=log,
                    dataset=dataset,
                    dataset_id=dataset_id,
                    road_filter_name=road_filter_name,
                    road_filter_segment=road_filter_segment,
                    head_req=head_req,
                    group_cols_in=group_cols_local,
                )
        elif map_req is not None:
            _save_road_aggregate_filter_from_df(
                df,
                log=log,
                dataset=dataset,
                dataset_id=dataset_id,
                road_filter_name=road_filter_name,
                road_filter_segment=road_filter_segment,
                head_req=head_req,
            )

        response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
        if reasoning:
            response_parts.append(f"Analysis Plan: {reasoning}\n")
        response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
        response_parts.append("Using pre-aggregated road statistics optimizer")
        if filters:
            response_parts.append(f"Filters applied: {filters}")
        if map_req is not None:
            response_parts.append("Map linkage: road lines derived from resulting road filter")
        if _query_requests_chart(reasoning, steps):
            auto_chart = _build_auto_chart_payload_from_df(
                df=df,
                reasoning=reasoning,
                steps=steps,
                chart_role="traffic_query_result",
                dataset_id=dataset_id or "__all__",
                group_cols=group_cols_local if groupby_req is not None else None,
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
    except Exception:
        return None


def _run_top_speed_roads_operation_impl(
    params: dict,
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    filters: list[dict],
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
    hard_brake_only: bool,
    apply_drivable_highway_default: bool,
    col_map: dict[str, str],
    from_sql: str,
    where_sql: str,
    where_params: list[Any],
    lat_expr: str,
    lon_expr: str,
    reasoning: str,
    active_run_ctx: dict[str, Any],
) -> str:
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    min_points = _bounded_int(params.get("min_points"), default=120, minimum=1, maximum=1_000_000)
    hotspot_limit = _bounded_int(
        params.get("hotspot_limit"),
        default=0,
        minimum=0,
        maximum=TRAFFIC_MAP_LIMIT_MAX,
    )
    chart_requested = bool(
        params.get("generate_chart")
        or params.get("include_chart")
        or params.get("with_chart")
    )

    stats_attempted = False
    df = pd.DataFrame()
    if not filters and not near_crash and not near_workzone and not hard_brake_only:
        with _db_conn() as conn:
            stats_candidates: list[str] = []
            stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_stats_mv"))
            stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_agg"))
            stats_candidates.extend(["viz_matched_roads_tbl", "public.viz_matched_roads_tbl"])
            stats_table = _first_existing_relation(conn, list(dict.fromkeys(stats_candidates)))
            if stats_table:
                stats_cols = _table_column_names(conn, stats_table)

                def _pick_stat_col(options: list[str]) -> Optional[str]:
                    for col in options:
                        if col in stats_cols:
                            return col
                    return None

                road_col = _pick_stat_col(["label", "road_name", "road", "name", "ref", "highway"])
                road_id_col = _pick_stat_col(["road_segment_id", "way_id", "segment_id", "road_id"])
                speed_col = _pick_stat_col(["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
                points_col = _pick_stat_col(["point_count", "points", "count"])
                std_col = _pick_stat_col(["speed_stddev_mph", "stddev_speed_mph", "speed_stddev"])
                speed_limit_col = _pick_stat_col(["speed_limit_mph", "speed_limit_mode", "avg_speed_limit_mph"])
                start_col = _pick_stat_col(["start_ts", "ts_start", "min_ts"])
                end_col = _pick_stat_col(["end_ts", "ts_end", "max_ts"])
                highway_col = _pick_stat_col(["highway"])

                if road_col and speed_col and points_col:
                    stats_attempted = True
                    where_stats = [f"{road_col} IS NOT NULL", f"{speed_col} IS NOT NULL", f"{points_col} IS NOT NULL"]
                    stats_params: list[Any] = []
                    if dataset_id and "dataset_id" in stats_cols:
                        where_stats.append("dataset_id = %s")
                        stats_params.append(dataset_id)
                    _append_drivable_highway_clause(
                        where_stats,
                        stats_params,
                        highway_col,
                        apply_drivable_highway_default=apply_drivable_highway_default,
                    )

                    road_id_select = f"MIN({road_id_col}::text)" if road_id_col else "NULL::text"
                    std_select = f"AVG({std_col})::float8" if std_col else "NULL::float8"
                    speed_limit_weighted = (
                        f"SUM({speed_limit_col} * {points_col}) / NULLIF(SUM({points_col}), 0)"
                        if speed_limit_col
                        else "NULL::float8"
                    )
                    start_select = f"MIN({start_col})" if start_col else "NULL::timestamptz"
                    end_select = f"MAX({end_col})" if end_col else "NULL::timestamptz"

                    stats_sql = f"""
                        WITH road_stats AS (
                            SELECT
                                {road_col} AS road_name,
                                {road_id_select} AS road_segment_id,
                                SUM({points_col})::bigint AS point_count,
                                (SUM({speed_col} * {points_col}) / NULLIF(SUM({points_col}), 0))::float8 AS avg_speed_mph,
                                {std_select} AS speed_stddev_mph,
                                ({speed_limit_weighted})::float8 AS avg_speed_limit_mph,
                                {start_select} AS start_ts,
                                {end_select} AS end_ts
                            FROM {stats_table}
                            WHERE {" AND ".join(where_stats)}
                            GROUP BY {road_col}
                            HAVING SUM({points_col}) >= %s
                        )
                        SELECT
                            ROW_NUMBER() OVER (ORDER BY avg_speed_mph DESC NULLS LAST, point_count DESC) AS rank,
                            road_name,
                            road_segment_id,
                            point_count,
                            avg_speed_mph,
                            speed_stddev_mph,
                            avg_speed_limit_mph,
                            (avg_speed_mph - avg_speed_limit_mph)::float8 AS avg_speed_over_limit_mph,
                            start_ts,
                            end_ts
                        FROM road_stats
                        ORDER BY rank
                        LIMIT %s
                    """
                    stats_params.extend([min_points, limit_n])
                    df = pd.read_sql_query(stats_sql, conn, params=stats_params)

    if not stats_attempted:
        rank_sql = f"""
            WITH filtered AS (
                SELECT
                    {col_map["road_name"]} AS road_name,
                    {col_map["road_segment_id"]} AS road_segment_id,
                    {col_map["speed"]} AS speed_mph,
                    {col_map["SpeedLimitMPH"]} AS speed_limit_mph,
                    {col_map["speed_over_limit"]} AS speed_over_limit_mph,
                    p.ts AS timestamp
                FROM {from_sql}
                WHERE {where_sql}
                  AND {col_map["road_name"]} IS NOT NULL
                  AND {col_map["speed"]} IS NOT NULL
                  {"AND LOWER(COALESCE(" + col_map["highway"] + "::text, '')) = ANY(%s)" if apply_drivable_highway_default and ("highway" in col_map) else ""}
            ),
            road_stats AS (
                SELECT
                    road_name,
                    MIN(road_segment_id)::text AS road_segment_id,
                    COUNT(*)::bigint AS point_count,
                    AVG(speed_mph)::float8 AS avg_speed_mph,
                    STDDEV_POP(speed_mph)::float8 AS speed_stddev_mph,
                    AVG(speed_limit_mph)::float8 AS avg_speed_limit_mph,
                    AVG(speed_over_limit_mph)::float8 AS avg_speed_over_limit_mph,
                    MIN(timestamp) AS start_ts,
                    MAX(timestamp) AS end_ts
                FROM filtered
                GROUP BY road_name
                HAVING COUNT(*) >= %s
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY avg_speed_mph DESC NULLS LAST, point_count DESC) AS rank,
                road_name,
                road_segment_id,
                point_count,
                avg_speed_mph,
                speed_stddev_mph,
                avg_speed_limit_mph,
                avg_speed_over_limit_mph,
                start_ts,
                end_ts
            FROM road_stats
            ORDER BY rank
            LIMIT %s
        """
        rank_params = list(where_params)
        if apply_drivable_highway_default and ("highway" in col_map):
            rank_params.append(list(DRIVABLE_HIGHWAY_TAGS))
        rank_params.extend([min_points, limit_n])
        with _db_conn() as conn:
            df = pd.read_sql_query(rank_sql, conn, params=rank_params)
    df = _apply_codebook_labels(df, dataset_id=dataset_id)

    top_road_names: list[str] = []
    top_segment_ids: list[str] = []
    if not df.empty and "road_name" in df.columns:
        seen_names: set[str] = set()
        for raw in df["road_name"].tolist():
            if raw is None:
                continue
            name = str(raw).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            top_road_names.append(name)
    if not df.empty and "road_segment_id" in df.columns:
        seen_segments: set[str] = set()
        for raw in df["road_segment_id"].tolist():
            if raw is None:
                continue
            seg = str(raw).strip()
            if not seg:
                continue
            if seg in seen_segments:
                continue
            seen_segments.add(seg)
            top_segment_ids.append(seg)

    if top_road_names and hotspot_limit > 0:
        hotspot_scope_clauses: list[str] = []
        hotspot_scope_params: list[Any] = []
        if top_segment_ids:
            hotspot_scope_clauses.append(f"({col_map['road_segment_id']})::text = ANY(%s::text[])")
            hotspot_scope_params.append(top_segment_ids)
        if top_road_names:
            hotspot_scope_clauses.append(f"LOWER({col_map['road_name']}) = ANY(%s::text[])")
            hotspot_scope_params.append([name.lower() for name in top_road_names])
        if not hotspot_scope_clauses:
            hotspot_rows = []
        else:
            hotspot_scope_sql = " OR ".join(hotspot_scope_clauses)
            hotspot_sql = f"""
                SELECT
                    {lat_expr} AS latitude,
                    {lon_expr} AS longitude,
                    p.ts AS timestamp,
                    {col_map["road_name"]} AS road_name,
                    {col_map["road_segment_id"]} AS road_segment_id,
                    {col_map["speed"]} AS speed,
                    {col_map["SpeedLimitMPH"]} AS "speedLimit",
                    {col_map["speed_over_limit"]} AS speed_over_limit,
                    {col_map["acc_x"]} AS acc_x,
                    {col_map["acc_y"]} AS acc_y
                FROM {from_sql}
                WHERE {where_sql}
                  AND {lat_expr} IS NOT NULL
                  AND {lon_expr} IS NOT NULL
                  AND ({hotspot_scope_sql})
                ORDER BY {col_map["speed_over_limit"]} DESC NULLS LAST, {col_map["speed"]} DESC NULLS LAST
                LIMIT %s
            """
            hotspot_params = list(where_params) + hotspot_scope_params + [hotspot_limit]
            with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(hotspot_sql, hotspot_params)
                hotspot_rows = cur.fetchall()
    else:
        hotspot_rows = []

    chart_payload: list[dict[str, Any]] = []
    if chart_requested and not df.empty:
        chart_df = df.copy()
        if "rank" in chart_df.columns:
            chart_df = chart_df.sort_values("rank", ascending=True)
        chart_df = chart_df.head(limit_n)

        road_labels: list[str] = []
        for idx, raw in enumerate(chart_df.get("road_name", pd.Series(dtype=object)).tolist()):
            name = str(raw).strip() if raw is not None else ""
            if not name:
                name = f"Road {idx + 1}"
            road_labels.append(name)

        avg_speed_values: list[Optional[float]] = []
        for raw in chart_df.get("avg_speed_mph", pd.Series(dtype=float)).tolist():
            avg_speed_values.append(float(raw) if pd.notna(raw) else None)

        point_counts: list[Optional[int]] = []
        for raw in chart_df.get("point_count", pd.Series(dtype=float)).tolist():
            point_counts.append(int(raw) if pd.notna(raw) else None)

        if road_labels and any(v is not None for v in avg_speed_values):
            chart_payload.append(
                {
                    "type": "bar",
                    "title": f"Top {len(road_labels)} Roads by Average Speed",
                    "xLabel": "Road",
                    "yLabel": "Average speed (mph)",
                    "xValues": road_labels,
                    "series": [
                        {
                            "label": "Avg speed (mph)",
                            "values": avg_speed_values,
                        },
                    ],
                    "orientation": "vertical",
                    "meta": {
                        "chartRole": "top_speed_roads",
                        "description": "Top-ranked roads by average speed for the active CV selection.",
                        "columnsUsed": [
                            "road_name",
                            "avg_speed_mph",
                            "point_count",
                        ],
                        "table": [
                            {
                                "road_name": road_labels[i],
                                "avg_speed_mph": avg_speed_values[i],
                                "point_count": point_counts[i],
                            }
                            for i in range(len(road_labels))
                        ],
                    },
                }
            )

    if top_road_names:
        map_label = f"Top {len(top_road_names)} roads by average speed"
        if hotspot_rows:
            map_label += " (hotspots)"
        map_payload = _make_map_payload(
            hotspot_rows,
            label=map_label,
            hard_brake_only=False,
        )
        map_payload["roadAggregateFilter"] = {
            "road_names": top_road_names[:40],
            "min_points": min_points,
            "limit": min(len(top_road_names), 40),
        }
        map_payload["overlay"] = bool(hotspot_rows)
        map_payload["analysis_type"] = "top_speed_roads"
        if chart_payload:
            map_payload["chartPayload"] = chart_payload
        save_map_for_session(map_payload, map_type="traffic")
    elif chart_payload:
        save_map_for_session(
            {
                "label": "Top speed roads",
                "count": 0,
                "points": [],
                "analysis_type": "top_speed_roads",
                "chartPayload": chart_payload,
            },
            map_type="traffic",
        )

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    run_id = active_run_ctx.get("run_id") if isinstance(active_run_ctx, dict) else None
    if run_id:
        response_parts.append(f"Active CV run: {run_id}")
    state_code = (active_run_ctx.get("state_code") or "").strip() if isinstance(active_run_ctx, dict) else ""
    if state_code:
        response_parts.append(f"State scope: {state_code}")
    response_parts.append("Ranking metric: average speed (mph), descending.")
    response_parts.append(f"Minimum point threshold enforced: {min_points} points/road.")
    if top_road_names:
        if hotspot_rows:
            response_parts.append(
                f"Map output: highlighted {len(top_road_names)} ranked roads and {len(hotspot_rows)} hotspot points."
            )
        else:
            response_parts.append(
                f"Map output: highlighted {len(top_road_names)} ranked roads."
            )

    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no roads met the minimum point threshold).")
    else:
        response_parts.append(f"Top {min(limit_n, len(df))} roads by average speed:")
        for _, row in df.head(limit_n).iterrows():
            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
            road_name = str(row.get("road_name") or "Unknown road").strip()
            point_count = int(row.get("point_count")) if pd.notna(row.get("point_count")) else 0
            avg_speed = float(row.get("avg_speed_mph")) if pd.notna(row.get("avg_speed_mph")) else 0.0
            if rank is None:
                response_parts.append(f"- {road_name}: {avg_speed:.2f} mph ({point_count:,} points)")
            else:
                response_parts.append(f"{rank}. {road_name}: {avg_speed:.2f} mph ({point_count:,} points)")
    return "\n".join(response_parts)


def _run_top_hard_braking_roads_operation_impl(
    params: dict,
    *,
    dataset_id: Optional[str],
    col_map: dict[str, str],
    from_sql: str,
    where_sql: str,
    where_params: list[Any],
    lat_expr: str,
    lon_expr: str,
    reasoning: str,
) -> str:
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    hotspot_limit = _bounded_int(
        params.get("hotspot_limit"),
        default=1200,
        minimum=100,
        maximum=TRAFFIC_MAP_LIMIT_MAX,
    )
    group_by = _normalize_top_hard_brake_group_by(params.get("group_by"))
    group_label = {
        "segment": "road segment",
        "road_name": "road name",
        "ref": "route ref",
    }.get(group_by, "road segment")
    road_ref_expr = col_map.get("road_ref", "NULL::text")
    where_sql_compact = " ".join(str(where_sql or "").split())

    # Iowa CV uploads are already route-segment aggregates. For prompts asking for
    # highest deceleration sums, answer directly from the uploaded aggregate table
    # instead of scanning hard-brake points or joining through way_id.
    if where_sql_compact in {"", "TRUE", "p.dataset_id = %s"}:
        try:
            with _db_conn() as conn:
                route_stats_table = _resolve_route_segment_stats_table(conn)
                route_stats_cols = _table_column_names(conn, route_stats_table) if route_stats_table else set()
                if route_stats_table and {"route_id", "decel_03g_sum"}.issubset(route_stats_cols):
                    speed_expr = (
                        "AVG(speed_mean_mph)::float8"
                        if "speed_mean_mph" in route_stats_cols
                        else "NULL::float8"
                    )
                    min_accel_expr = (
                        "MIN(acceleration_min)::float8"
                        if "acceleration_min" in route_stats_cols
                        else "NULL::float8"
                    )
                    start_expr = (
                        "MIN(timestamp_5min)"
                        if "timestamp_5min" in route_stats_cols
                        else "NULL::timestamptz"
                    )
                    end_expr = (
                        "MAX(timestamp_5min)"
                        if "timestamp_5min" in route_stats_cols
                        else "NULL::timestamptz"
                    )
                    rank_sql = f"""
                        WITH route_stats AS (
                            SELECT
                                route_id::text AS road_segment_id,
                                route_id::text AS road_ref,
                                route_id::text AS road_name,
                                SUM(COALESCE(decel_03g_sum, 0))::bigint AS hard_brake_count,
                                {min_accel_expr} AS min_acc_x,
                                {speed_expr} AS avg_speed_mph,
                                {start_expr} AS start_ts,
                                {end_expr} AS end_ts
                            FROM {route_stats_table}
                            WHERE route_id IS NOT NULL
                            GROUP BY route_id
                            HAVING SUM(COALESCE(decel_03g_sum, 0)) > 0
                        )
                        SELECT
                            ROW_NUMBER() OVER (
                                ORDER BY hard_brake_count DESC, road_name ASC
                            ) AS rank,
                            road_name,
                            road_segment_id,
                            road_ref,
                            hard_brake_count,
                            NULL::float8 AS avg_acc_x,
                            min_acc_x,
                            avg_speed_mph,
                            NULL::float8 AS avg_speed_over_limit_mph,
                            start_ts,
                            end_ts
                        FROM route_stats
                        ORDER BY rank
                        LIMIT %s
                    """
                    df = pd.read_sql_query(rank_sql, conn, params=[limit_n])
                    df = _apply_codebook_labels(df, dataset_id=dataset_id)

                    top_segment_ids = [
                        str(raw).strip()
                        for raw in df.get("road_segment_id", pd.Series(dtype=object)).tolist()
                        if str(raw or "").strip()
                    ]
                    if top_segment_ids:
                        map_payload = _make_map_payload(
                            [],
                            label=f"Top {len(top_segment_ids)} routes by deceleration sum",
                            hard_brake_only=True,
                        )
                        map_payload["roadAggregateFilter"] = {
                            "road_segment_ids": top_segment_ids[:40],
                            "road_segment_id": top_segment_ids[0],
                            "limit": min(len(top_segment_ids), 40),
                            "group_by": "route_id",
                            "metric": "decel_03g_sum",
                        }
                        map_payload["analysis_type"] = "top_hard_braking_roads"
                        map_payload["overlay"] = False
                        save_map_for_session(map_payload, map_type="traffic")

                    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
                    if reasoning:
                        response_parts.append(f"Analysis Plan: {reasoning}\n")
                    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
                    response_parts.append("Source: uploaded Iowa CV route segment aggregate table.")
                    response_parts.append("Ranking metric: SUM(decel_03g_sum), descending.")
                    if top_segment_ids:
                        response_parts.append(f"Map output: highlighted {len(top_segment_ids)} ranked routes.")

                    response_parts.append("\nFINAL DATA RESULTS:")
                    if df.empty:
                        response_parts.append("Result is an empty table (no deceleration sums found).")
                    else:
                        response_parts.append(
                            f"Top {min(limit_n, len(df))} routes by deceleration sum (hard brakes):"
                        )
                        for _, row in df.head(limit_n).iterrows():
                            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
                            route_id = str(row.get("road_name") or "Unknown route").strip()
                            event_count = int(row.get("hard_brake_count")) if pd.notna(row.get("hard_brake_count")) else 0
                            if rank is None:
                                response_parts.append(f"- {route_id}: {event_count:,}")
                            else:
                                response_parts.append(f"{rank}. {route_id}: {event_count:,}")
                    return "\n".join(response_parts)
        except Exception:
            pass

    if group_by == "segment":
        road_stats_group_expr = "road_segment_id"
        road_stats_group_by_clause = "group_key, road_segment_id"
        road_stats_where = "WHERE road_segment_id IS NOT NULL"
        road_name_agg_expr = (
            "CASE "
            "WHEN COALESCE(MAX(road_name), '') = '' THEN 'Segment #' || road_segment_id "
            "ELSE MAX(road_name) || ' (#' || road_segment_id || ')' "
            "END"
        )
        road_segment_agg_expr = "road_segment_id"
        road_ref_agg_expr = "MAX(road_ref)"
    elif group_by == "ref":
        road_stats_group_expr = "LOWER(COALESCE(road_ref, '[unknown ref]'))"
        road_stats_group_by_clause = "group_key"
        road_stats_where = ""
        road_name_agg_expr = (
            "CASE "
            "WHEN COALESCE(MAX(road_ref), '') = '' THEN COALESCE(MAX(road_name), '[unknown road]') "
            "WHEN COALESCE(MAX(road_name), '') = '' THEN COALESCE(MAX(road_ref), '[unknown ref]') "
            "ELSE COALESCE(MAX(road_ref), '[unknown ref]') || ' | ' || COALESCE(MAX(road_name), '[unknown road]') "
            "END"
        )
        road_segment_agg_expr = "MIN(road_segment_id)::text"
        road_ref_agg_expr = "COALESCE(MAX(road_ref), '[unknown ref]')"
    else:
        road_stats_group_expr = "LOWER(COALESCE(road_name, '[unknown road]'))"
        road_stats_group_by_clause = "group_key"
        road_stats_where = ""
        road_name_agg_expr = "COALESCE(MAX(road_name), '[unknown road]')"
        road_segment_agg_expr = "MIN(road_segment_id)::text"
        road_ref_agg_expr = "MAX(road_ref)"

    rank_sql = f"""
        WITH filtered AS (
            SELECT
                NULLIF(TRIM(CAST({col_map["road_name"]} AS text)), '') AS road_name,
                NULLIF(TRIM(CAST({col_map["road_segment_id"]} AS text)), '') AS road_segment_id,
                NULLIF(TRIM(CAST({road_ref_expr} AS text)), '') AS road_ref,
                {col_map["acc_x"]} AS acc_x,
                {col_map["speed"]} AS speed_mph,
                {col_map["speed_over_limit"]} AS speed_over_limit_mph,
                p.ts AS timestamp
            FROM {from_sql}
            WHERE {where_sql}
        ),
        road_stats AS (
            SELECT
                {road_stats_group_expr} AS group_key,
                {road_segment_agg_expr} AS road_segment_id,
                {road_ref_agg_expr} AS road_ref,
                {road_name_agg_expr} AS road_name,
                COUNT(*)::bigint AS hard_brake_count,
                AVG(acc_x)::float8 AS avg_acc_x,
                MIN(acc_x)::float8 AS min_acc_x,
                AVG(speed_mph)::float8 AS avg_speed_mph,
                AVG(speed_over_limit_mph)::float8 AS avg_speed_over_limit_mph,
                MIN(timestamp) AS start_ts,
                MAX(timestamp) AS end_ts
            FROM filtered
            {road_stats_where}
            GROUP BY {road_stats_group_by_clause}
        )
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY hard_brake_count DESC, ABS(COALESCE(avg_acc_x, 0.0)) DESC, road_name ASC
            ) AS rank,
            road_name,
            road_segment_id,
            road_ref,
            hard_brake_count,
            avg_acc_x,
            min_acc_x,
            avg_speed_mph,
            avg_speed_over_limit_mph,
            start_ts,
            end_ts
        FROM road_stats
        ORDER BY rank
        LIMIT %s
    """
    with _db_conn() as conn:
        df = pd.read_sql_query(rank_sql, conn, params=list(where_params) + [limit_n])
    df = _apply_codebook_labels(df, dataset_id=dataset_id)

    top_road_names: list[str] = []
    top_segment_ids: list[str] = []
    top_refs: list[str] = []
    if not df.empty:
        if "road_name" in df.columns:
            seen_names: set[str] = set()
            for raw in df["road_name"].tolist():
                if raw is None:
                    continue
                name = str(raw).strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                top_road_names.append(name)
        if "road_segment_id" in df.columns:
            seen_segments: set[str] = set()
            for raw in df["road_segment_id"].tolist():
                seg = str(raw or "").strip()
                if not seg:
                    continue
                if seg in seen_segments:
                    continue
                seen_segments.add(seg)
                top_segment_ids.append(seg)
        if "road_ref" in df.columns:
            seen_refs: set[str] = set()
            for raw in df["road_ref"].tolist():
                ref = str(raw or "").strip()
                if not ref:
                    continue
                key = ref.lower()
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                top_refs.append(ref)

    hotspot_rows: list[dict[str, Any]] = []
    scope_count = 0
    if top_road_names or top_segment_ids or top_refs:
        scope_clauses: list[str] = []
        scope_params: list[Any] = []
        if group_by == "segment" and top_segment_ids:
            scope_clauses.append(
                f"NULLIF(TRIM(CAST({col_map['road_segment_id']} AS text)), '') = ANY(%s::text[])"
            )
            scope_params.append(top_segment_ids)
            scope_count = len(top_segment_ids)
        elif group_by == "ref" and top_refs:
            scope_clauses.append(
                f"LOWER(COALESCE(NULLIF(TRIM(CAST({road_ref_expr} AS text)), ''), '[unknown ref]')) = ANY(%s::text[])"
            )
            scope_params.append([ref.lower() for ref in top_refs])
            scope_count = len(top_refs)
        elif top_road_names:
            scope_clauses.append(f"LOWER({col_map['road_name']}) = ANY(%s::text[])")
            scope_params.append([name.lower() for name in top_road_names])
            scope_count = len(top_road_names)

        hotspot_sql = f"""
            SELECT
                {lat_expr} AS latitude,
                {lon_expr} AS longitude,
                p.ts AS timestamp,
                {col_map["road_name"]} AS road_name,
                {col_map["road_segment_id"]} AS road_segment_id,
                {road_ref_expr} AS road_ref,
                {col_map["speed"]} AS speed,
                {col_map["SpeedLimitMPH"]} AS "speedLimit",
                {col_map["speed_over_limit"]} AS speed_over_limit,
                {col_map["acc_x"]} AS acc_x,
                {col_map["acc_y"]} AS acc_y,
                'HardBrake'::text AS type,
                'HardBrake'::text AS point_type
            FROM {from_sql}
            WHERE {where_sql}
              AND {lat_expr} IS NOT NULL
              AND {lon_expr} IS NOT NULL
              AND ({' OR '.join(scope_clauses)})
            ORDER BY ABS({col_map["acc_x"]}) DESC NULLS LAST, p.ts DESC
            LIMIT %s
        """
        hotspot_params = list(where_params) + scope_params + [hotspot_limit]
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(hotspot_sql, hotspot_params)
            hotspot_rows = cur.fetchall()

        map_payload = _make_map_payload(
            hotspot_rows,
            label=f"Top {scope_count or len(top_road_names)} groups by hard-braking events ({group_label})",
            hard_brake_only=True,
        )
        road_scope_filter: dict[str, Any] = {
            "min_points": 1,
            "limit": min(max(scope_count, len(top_road_names), len(top_segment_ids), len(top_refs)), 40),
            "group_by": group_by,
        }
        if top_road_names and group_by == "road_name":
            road_scope_filter["road_names"] = top_road_names[:40]
        if top_segment_ids:
            road_scope_filter["road_segment_ids"] = top_segment_ids[:40]
            road_scope_filter["road_segment_id"] = top_segment_ids[0]
        if top_refs:
            road_scope_filter["road_refs"] = top_refs[:40]
        map_payload["roadAggregateFilter"] = road_scope_filter
        map_payload["analysis_type"] = "top_hard_braking_roads"
        map_payload["overlay"] = bool(hotspot_rows)
        save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    response_parts.append("Ranking metric: hard-braking event count, descending.")
    response_parts.append(f"Grouping: {group_label}.")
    if scope_count > 0:
        response_parts.append(
            f"Map output: highlighted {scope_count} ranked groups and {len(hotspot_rows)} hard-braking points."
        )

    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no hard-braking events matched the filters).")
    else:
        response_parts.append(
            f"Top {min(limit_n, len(df))} groups by hard-braking events (grouped by {group_label}):"
        )
        for _, row in df.head(limit_n).iterrows():
            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
            road_name = str(row.get("road_name") or "Unknown road").strip()
            event_count = int(row.get("hard_brake_count")) if pd.notna(row.get("hard_brake_count")) else 0
            avg_acc_x = float(row.get("avg_acc_x")) if pd.notna(row.get("avg_acc_x")) else 0.0
            if rank is None:
                response_parts.append(
                    f"- {road_name}: {event_count:,} events (avg acc_x {avg_acc_x:.3f} g)"
                )
            else:
                response_parts.append(
                    f"{rank}. {road_name}: {event_count:,} events (avg acc_x {avg_acc_x:.3f} g)"
                )
    return "\n".join(response_parts)

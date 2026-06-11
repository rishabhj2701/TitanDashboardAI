"""Conflation SQL/domain operations."""

from ..services.conflation_service import (
    build_conflation_response,
    get_dataset_info,
    is_hard_braking_ref,
    resolve_dataset_id_generic,
)
from ..services.datasets import show_datasets_on_map
from ..services.maps import save_conflation_map
from ..session_state import get_active_user
from .common import (
    CRASH_TIMEZONE,
    _cv_relation_candidates,
    _db_conn,
    _first_existing_relation,
    _resolve_hard_brake_table,
    get_active_session,
    logging,
    pd,
)

def run_multi_dataset_conflation(
    dataset_names: list[str],
    max_distance_meters: float = 500.0,
    time_mode: str = "auto",
    time_window_minutes: int = 60,
    limit: int = 5000,
) -> str:
    """
    Find spatial-temporal matches across MULTIPLE datasets (not just two).

    This finds records that are near each other across all specified datasets.
    For example: crashes near workzones near traffic incidents.

    Args:
        dataset_names: List of dataset names to conflate (e.g., ["crashes", "workzones", "traffic"])
        max_distance_meters: Maximum distance for spatial match
        time_mode: "auto", "window", "overlap", or "none"
        time_window_minutes: Time buffer for window mode
        limit: Maximum matches to return

    Returns:
        Summary of matches found
    """
    if len(dataset_names) < 2:
        return "❌ ERROR: Need at least 2 datasets to conflate"

    # For multi-dataset conflation, we do pairwise joins starting from the first dataset
    # Each subsequent dataset is joined to the previous result

    results = []

    # Start with first two datasets
    base_result = run_generic_conflation(
        left_dataset=dataset_names[0],
        right_dataset=dataset_names[1],
        max_distance_meters=max_distance_meters,
        time_mode=time_mode,
        time_window_minutes=time_window_minutes,
        generate_map=False,  # We'll generate one combined map at the end
        limit=limit,
    )
    results.append(f"**{dataset_names[0]} ↔ {dataset_names[1]}:**")
    results.append(base_result)

    # If more than 2 datasets, we report pairwise results
    # (A full N-way join would be very complex, so we do pairwise analysis)
    if len(dataset_names) > 2:
        results.append("")
        results.append("**Additional pairwise analysis:**")

        for i in range(2, len(dataset_names)):
            # Compare new dataset with first dataset
            pair_result = run_generic_conflation(
                left_dataset=dataset_names[0],
                right_dataset=dataset_names[i],
                max_distance_meters=max_distance_meters,
                time_mode=time_mode,
                time_window_minutes=time_window_minutes,
                generate_map=False,
                limit=limit,
            )
            results.append(f"\n**{dataset_names[0]} ↔ {dataset_names[i]}:**")
            results.append(pair_result)

    # Generate combined map of all datasets
    show_datasets_on_map(dataset_names, limit_per_dataset=1000)
    results.append("")
    results.append("A combined map showing all datasets has been generated.")

    return "\n".join(results)


def _apply_hard_braking_source(
    left_info: dict,
    right_info: dict,
    left_hard_braking: bool,
    right_hard_braking: bool,
) -> tuple[dict, dict]:
    if not (left_hard_braking or right_hard_braking):
        return left_info, right_info

    with _db_conn() as conn:
        hb_table = _resolve_hard_brake_table(conn)
    if not hb_table:
        return left_info, right_info

    def _override(info: dict) -> dict:
        with _db_conn() as conn:
            hb_cols = {
                str(r[0]).strip().lower()
                for r in _table_cols(conn, hb_table)
            }
        has_dataset_filter = "dataset_id" in hb_cols
        id_col = "point_id" if "point_id" in hb_cols else ("id" if "id" in hb_cols else info.get("id_col", "id"))
        time_col = "ts" if "ts" in hb_cols else ("timestamp" if "timestamp" in hb_cols else info.get("time_col", "ts"))
        props_col = "attrs" if "attrs" in hb_cols else ("props" if "props" in hb_cols else info.get("props_col", "attrs"))
        lat_col = "lat" if "lat" in hb_cols else ("latitude" if "latitude" in hb_cols else info.get("lat_col"))
        lon_col = "lon" if "lon" in hb_cols else ("longitude" if "longitude" in hb_cols else info.get("lon_col"))
        speed_col = "speed" if "speed" in hb_cols else ("speed_mph" if "speed_mph" in hb_cols else info.get("speed_col"))
        speed_limit_col = (
            "speed_limit_mph"
            if "speed_limit_mph" in hb_cols
            else ("speed_limit" if "speed_limit" in hb_cols else info.get("speed_limit_col"))
        )
        acc_x_col = (
            "accx"
            if "accx" in hb_cols
            else ("acc_x" if "acc_x" in hb_cols else ("accel_x" if "accel_x" in hb_cols else info.get("acc_x_col")))
        )
        acc_y_col = (
            "accy"
            if "accy" in hb_cols
            else ("acc_y" if "acc_y" in hb_cols else ("accel_y" if "accel_y" in hb_cols else info.get("acc_y_col")))
        )
        road_segment_col = (
            "road_segment_id"
            if "road_segment_id" in hb_cols
            else ("way_id" if "way_id" in hb_cols else info.get("road_segment_col"))
        )
        return {
            **info,
            "entity_type": "hard_braking",
            "table": hb_table,
            "props_col": props_col,
            "id_col": id_col,
            "time_col": time_col,
            "lat_col": lat_col,
            "lon_col": lon_col,
            "speed_col": speed_col,
            "speed_limit_col": speed_limit_col,
            "acc_x_col": acc_x_col,
            "acc_y_col": acc_y_col,
            "road_segment_col": road_segment_col,
            "geom_m_col": "geom_m" if "geom_m" in hb_cols else None,
            "geom_3857_col": "geom_3857" if "geom_3857" in hb_cols else None,
            "geom_4326_col": "geom_4326" if "geom_4326" in hb_cols else ("geom" if "geom" in hb_cols else None),
            "has_date_range": False,
            "has_dataset_filter": has_dataset_filter,
            "dataset_filter_id": info.get("dataset_filter_id") if has_dataset_filter else None,
        }

    if left_hard_braking:
        left_info = _override(left_info)
    if right_hard_braking:
        right_info = _override(right_info)
    return left_info, right_info


def _normalize_conflation_time_mode(time_mode: str, left_info: dict, right_info: dict) -> str:
    if time_mode != "auto":
        return time_mode
    if left_info.get("has_date_range") or right_info.get("has_date_range"):
        return "overlap"
    return "window"


def _nullif_trim_text_expr(expr: str) -> str:
    return f"NULLIF(TRIM(({expr})::text), '')"


def _normalized_route_ref_expr(ref_expr: str) -> str:
    # Normalize route references consistently (e.g., I 70 -> I-70, US 63 -> US-63).
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


def _preferred_road_name_expr(
    *,
    ref_expr: str | None = None,
    label_expr: str | None = None,
    name_expr: str | None = None,
    highway_expr: str | None = None,
    extra_exprs: list[str] | None = None,
) -> str:
    parts: list[str] = []
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


def _build_conflation_select_sql(
    left_info: dict,
    right_info: dict,
    left_time_expr: str,
    right_time_expr: str,
    road_stats_table: str | None,
    roads_table: str | None,
    left_alias: str,
    right_alias: str,
) -> tuple[str, str, str]:
    left_lat = _lat_expr(left_info, left_alias)
    left_lon = _lon_expr(left_info, left_alias)
    left_road_segment = _road_segment_expr(left_info, left_alias)
    right_lat = _lat_expr(right_info, right_alias)
    right_lon = _lon_expr(right_info, right_alias)
    right_road_segment = _road_segment_expr(right_info, right_alias)
    left_select = f"""
        {left_alias}.{left_info['id_col']} AS left_id,
        {left_lat} AS left_latitude,
        {left_lon} AS left_longitude,
        {left_time_expr} AS left_time,
        {left_road_segment} AS left_road_segment_id,
        {_speed_expr(left_info, left_alias)} AS left_speed,
        {_speed_limit_expr(left_info, left_alias, road_stats_table)} AS left_speed_limit,
        {_acc_x_expr(left_info, left_alias)} AS left_acc_x,
        {_acc_y_expr(left_info, left_alias)} AS left_acc_y,
        {_props_expr(left_info, left_alias)} AS left_props
    """
    right_select = f"""
        {right_alias}.{right_info['id_col']} AS right_id,
        {right_lat} AS right_latitude,
        {right_lon} AS right_longitude,
        {right_time_expr} AS right_time,
        {right_road_segment} AS right_road_segment_id,
        {_speed_expr(right_info, right_alias)} AS right_speed,
        {_speed_limit_expr(right_info, right_alias, road_stats_table)} AS right_speed_limit,
        {_acc_x_expr(right_info, right_alias)} AS right_acc_x,
        {_acc_y_expr(right_info, right_alias)} AS right_acc_y,
        {_props_expr(right_info, right_alias)} AS right_props
    """
    distance_calc = (
        f"ST_Distance({_geom_m_expr(left_info, left_alias)}, {_geom_m_expr(right_info, right_alias)}) "
        "AS distance_meters"
    )
    return left_select, right_select, distance_calc


def _build_conflation_time_sql(
    time_mode: str,
    time_window_minutes: int,
    left_info: dict,
    right_info: dict,
    left_time_expr: str,
    right_time_expr: str,
    left_alias: str,
    right_alias: str,
) -> tuple[str, str]:
    if time_mode == "overlap":
        if left_info.get("has_date_range"):
            time_condition = f"""
                ({right_time_expr} >=
                    COALESCE(({left_alias}.{left_info['props_col']}->>'start_date')::timestamp, '1900-01-01'::timestamp)
                AND {right_time_expr} <=
                    COALESCE(({left_alias}.{left_info['props_col']}->>'end_date')::timestamp, '2100-01-01'::timestamp))
            """
            time_delta = f"""
                EXTRACT(EPOCH FROM ({right_time_expr} -
                    COALESCE(({left_alias}.{left_info['props_col']}->>'start_date')::timestamp, {right_time_expr}))) / 60.0
                AS time_delta_minutes
            """
            return time_condition, time_delta

        if right_info.get("has_date_range"):
            time_condition = f"""
                ({left_time_expr} >=
                    COALESCE(({right_alias}.{right_info['props_col']}->>'start_date')::timestamp, '1900-01-01'::timestamp)
                AND {left_time_expr} <=
                    COALESCE(({right_alias}.{right_info['props_col']}->>'end_date')::timestamp, '2100-01-01'::timestamp))
            """
            time_delta = f"""
                EXTRACT(EPOCH FROM ({left_time_expr} -
                    COALESCE(({right_alias}.{right_info['props_col']}->>'start_date')::timestamp, {left_time_expr}))) / 60.0
                AS time_delta_minutes
            """
            return time_condition, time_delta

    if time_mode in {"window", "overlap"}:
        time_condition = f"""
            ABS(EXTRACT(EPOCH FROM ({left_time_expr} - {right_time_expr}))) <= {time_window_minutes * 60}
        """
        time_delta = f"""
            ABS(EXTRACT(EPOCH FROM ({left_time_expr} - {right_time_expr}))) / 60.0 AS time_delta_minutes
        """
        return time_condition, time_delta

    return "TRUE", "NULL::float8 AS time_delta_minutes"


def _build_conflation_where_sql(
    left_info: dict,
    right_info: dict,
    left_alias: str,
    right_alias: str,
) -> tuple[str, str]:
    left_table = left_info["table"]
    right_table = right_info["table"]
    left_where = "TRUE"
    right_where = "TRUE"
    if left_info.get("has_dataset_filter", True):
        left_where = f"{left_alias}.dataset_id = %s"
    if right_info.get("has_dataset_filter", True):
        right_where = f"{right_alias}.dataset_id = %s"
    if left_table == "events":
        left_where += f" AND {left_alias}.owner_user_id = %s"
    if right_table == "events":
        right_where += f" AND {right_alias}.owner_user_id = %s"
    return left_where, right_where


def _build_conflation_sql(
    left_info: dict,
    right_info: dict,
    time_mode: str,
    time_window_minutes: int,
    road_stats_table: str | None = None,
    roads_table: str | None = None,
    left_alias: str = "l",
    right_alias: str = "r",
) -> str:
    left_table = left_info["table"]
    right_table = right_info["table"]
    left_time_expr = _time_expr(left_info, left_alias)
    right_time_expr = _time_expr(right_info, right_alias)
    left_select, right_select, distance_calc = _build_conflation_select_sql(
        left_info,
        right_info,
        left_time_expr,
        right_time_expr,
        road_stats_table,
        roads_table,
        left_alias,
        right_alias,
    )
    time_condition, time_delta = _build_conflation_time_sql(
        time_mode=time_mode,
        time_window_minutes=time_window_minutes,
        left_info=left_info,
        right_info=right_info,
        left_time_expr=left_time_expr,
        right_time_expr=right_time_expr,
        left_alias=left_alias,
        right_alias=right_alias,
    )
    left_where, right_where = _build_conflation_where_sql(
        left_info=left_info,
        right_info=right_info,
        left_alias=left_alias,
        right_alias=right_alias,
    )
    left_geom_m = _geom_m_expr(left_info, left_alias)
    right_geom_m = _geom_m_expr(right_info, right_alias)

    return f"""
        SELECT
            {left_select},
            {right_select},
            {distance_calc},
            {time_delta}
        FROM {left_table} {left_alias}
        JOIN {right_table} {right_alias}
            ON ST_DWithin({left_geom_m}, {right_geom_m}, %s)
            AND {time_condition}
        WHERE {left_where}
            AND {right_where}
            AND {_lat_expr(left_info, left_alias)} IS NOT NULL
            AND {_lat_expr(right_info, right_alias)} IS NOT NULL
        ORDER BY distance_meters ASC
        LIMIT %s
    """


def _build_conflation_params(
    max_distance_meters: float,
    left_info: dict,
    right_info: dict,
    left_table: str,
    right_table: str,
    owner_user_id: str,
    limit: int,
) -> list:
    params = [max_distance_meters]
    if left_info.get("has_dataset_filter", True):
        params.append(left_info.get("dataset_filter_id") or left_info.get("dataset_id"))
    if left_table == "events":
        params.append(owner_user_id)
    if right_info.get("has_dataset_filter", True):
        params.append(right_info.get("dataset_filter_id") or right_info.get("dataset_id"))
    if right_table == "events":
        params.append(owner_user_id)
    params.append(limit)
    return params


def _table_cols(conn, table_name: str) -> list[tuple[str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass(%s)
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (table_name,),
        )
        return cur.fetchall()


def _lat_expr(info: dict, alias: str) -> str:
    lat_col = info.get("lat_col")
    if lat_col:
        return f"{alias}.{lat_col}"
    geom_4326_col = info.get("geom_4326_col")
    if geom_4326_col:
        return f"ST_Y({alias}.{geom_4326_col})"
    geom_3857_col = info.get("geom_3857_col")
    if geom_3857_col:
        return f"ST_Y(ST_Transform({alias}.{geom_3857_col}, 4326))"
    geom_m_col = info.get("geom_m_col")
    if geom_m_col:
        return f"ST_Y(ST_Transform({alias}.{geom_m_col}, 4326))"
    return "NULL::float8"


def _lon_expr(info: dict, alias: str) -> str:
    lon_col = info.get("lon_col")
    if lon_col:
        return f"{alias}.{lon_col}"
    geom_4326_col = info.get("geom_4326_col")
    if geom_4326_col:
        return f"ST_X({alias}.{geom_4326_col})"
    geom_3857_col = info.get("geom_3857_col")
    if geom_3857_col:
        return f"ST_X(ST_Transform({alias}.{geom_3857_col}, 4326))"
    geom_m_col = info.get("geom_m_col")
    if geom_m_col:
        return f"ST_X(ST_Transform({alias}.{geom_m_col}, 4326))"
    return "NULL::float8"


def _road_segment_expr(info: dict, alias: str) -> str:
    road_col = info.get("road_segment_col")
    if road_col:
        return f"{alias}.{road_col}::text"
    return "NULL::text"


def _props_expr(info: dict, alias: str) -> str:
    props_col = info.get("props_col")
    if props_col:
        return f"{alias}.{props_col}"
    return "'{}'::jsonb"


def _json_props_expr(info: dict, alias: str) -> str:
    props_col = info.get("props_col")
    if props_col:
        return f"COALESCE({alias}.{props_col}, '{{}}'::jsonb)"
    return "'{}'::jsonb"


def _numeric_col_expr(alias: str, col_name: str | None) -> str:
    if not col_name:
        return "NULL::float8"
    return f"NULLIF({alias}.{col_name}::text, '')::float8"


def _speed_expr(info: dict, alias: str) -> str:
    props = _json_props_expr(info, alias)
    speed_col = info.get("speed_col")
    return (
        "COALESCE("
        f"{_numeric_col_expr(alias, speed_col)}, "
        f"NULLIF({props}->>'speed', '')::float8, "
        f"NULLIF({props}->>'speed_mph', '')::float8, "
        f"NULLIF({props}->>'Speed', '')::float8"
        ")"
    )


def _speed_limit_expr(info: dict, alias: str, road_stats_table: str | None = None) -> str:
    props = _json_props_expr(info, alias)
    speed_limit_col = info.get("speed_limit_col")
    road_fallback = _road_stats_speed_limit_expr(info, alias, road_stats_table)
    return (
        "COALESCE("
        f"{_numeric_col_expr(alias, speed_limit_col)}, "
        f"NULLIF({props}->>'SpeedLimitMPH', '')::float8, "
        f"NULLIF({props}->>'speedLimit', '')::float8, "
        f"NULLIF({props}->>'SpeedLimit', '')::float8, "
        f"NULLIF({props}->>'speed_limit_mph', '')::float8, "
        f"NULLIF({props}->>'speed_limit', '')::float8, "
        f"{road_fallback}"
        ")"
    )


def _road_stats_speed_limit_expr(info: dict, alias: str, road_stats_table: str | None) -> str:
    if not road_stats_table:
        return "NULL::float8"
    road_expr = _road_segment_expr(info, alias)
    return (
        f"CASE WHEN ({road_expr}) ~ '^[0-9]+$' THEN ("
        f"SELECT rs.speed_limit_mph::float8 "
        f"FROM {road_stats_table} rs "
        f"WHERE rs.way_id = ({road_expr})::bigint "
        f"  AND rs.speed_limit_mph IS NOT NULL "
        f"ORDER BY rs.point_count DESC NULLS LAST "
        f"LIMIT 1"
        f") ELSE NULL::float8 END"
    )


def _resolve_conflation_road_stats_table() -> str | None:
    with _db_conn() as conn:
        candidates = _cv_relation_candidates(conn, "cv_road_stats_mv")
        if "public.cv_road_stats_mv" not in candidates:
            candidates.append("public.cv_road_stats_mv")
        return _first_existing_relation(conn, candidates)


def _resolve_conflation_roads_table() -> str | None:
    with _db_conn() as conn:
        candidates: list[str] = []
        candidates.extend(_cv_relation_candidates(conn, "roads"))
        if "public.roads" not in candidates:
            candidates.append("public.roads")
        table = _first_existing_relation(conn, candidates)
        if not table:
            return None
        cols = {str(r[0]).strip().lower() for r in _table_cols(conn, table)}
        if "road_segment_id" not in cols:
            return None
        return table


def _time_expr(info: dict, alias: str) -> str:
    time_col = info.get("time_col")
    base_expr = f"{alias}.{time_col}" if time_col else "NULL::timestamptz"
    entity = str(info.get("entity_type") or "").strip().lower()
    table = str(info.get("table") or "")
    props_col = info.get("props_col")
    if not props_col:
        return base_expr
    if entity not in {"crash", "accident"} and table != "events":
        return base_expr

    props = _json_props_expr(info, alias)
    date_text = (
        f"COALESCE("
        f"NULLIF({props}->>'_event_date_norm',''), "
        f"NULLIF({props}->>'accident_date',''), "
        f"NULLIF({props}->>'event_date',''), "
        f"NULLIF({props}->>'crash_date','')"
        f")"
    )
    time_text = (
        f"COALESCE("
        f"NULLIF({props}->>'_event_time_norm',''), "
        f"NULLIF({props}->>'accident_time',''), "
        f"NULLIF({props}->>'event_time',''), "
        f"NULLIF({props}->>'crash_time','')"
        f")"
    )
    derived_expr = (
        f"CASE "
        f"WHEN ({date_text}) IS NOT NULL AND ("
        f"  NULLIF(SUBSTRING(({time_text}) FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL "
        f"  OR NULLIF(SUBSTRING(({time_text}) FROM '([0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL"
        f") THEN ("
        f"  ({date_text})::date + COALESCE("
        f"    NULLIF(SUBSTRING(({time_text}) FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '')::time, "
        f"    NULLIF(SUBSTRING(({time_text}) FROM '([0-9]{{2}}:[0-9]{{2}})'), '')::time"
        f"  )"
        f") AT TIME ZONE '{CRASH_TIMEZONE}' "
        f"WHEN ({time_text}) ~ 'T' THEN ({time_text})::timestamptz "
        f"ELSE NULL::timestamptz END"
    )
    return f"COALESCE({derived_expr}, {base_expr})"


def _acc_x_expr(info: dict, alias: str) -> str:
    props = _json_props_expr(info, alias)
    acc_x_col = info.get("acc_x_col")
    return (
        "COALESCE("
        f"{_numeric_col_expr(alias, acc_x_col)}, "
        f"NULLIF({props}->>'acc_x', '')::float8, "
        f"NULLIF({props}->>'accx', '')::float8, "
        f"NULLIF({props}->>'AccX', '')::float8"
        ")"
    )


def _acc_y_expr(info: dict, alias: str) -> str:
    props = _json_props_expr(info, alias)
    acc_y_col = info.get("acc_y_col")
    return (
        "COALESCE("
        f"{_numeric_col_expr(alias, acc_y_col)}, "
        f"NULLIF({props}->>'acc_y', '')::float8, "
        f"NULLIF({props}->>'accy', '')::float8, "
        f"NULLIF({props}->>'AccY', '')::float8"
        ")"
    )


def _geom_m_expr(info: dict, alias: str) -> str:
    geom_m_col = info.get("geom_m_col")
    if geom_m_col:
        return f"{alias}.{geom_m_col}"
    geom_3857_col = info.get("geom_3857_col")
    if geom_3857_col:
        return f"ST_Transform({alias}.{geom_3857_col}, 26915)"
    geom_4326_col = info.get("geom_4326_col")
    if geom_4326_col:
        return f"ST_Transform({alias}.{geom_4326_col}, 26915)"
    lon_col = info.get("lon_col")
    lat_col = info.get("lat_col")
    if lon_col and lat_col:
        return f"ST_Transform(ST_SetSRID(ST_MakePoint({alias}.{lon_col}, {alias}.{lat_col}), 4326), 26915)"
    raise ValueError(f"Dataset table '{info.get('table')}' has no usable geometry for conflation.")


def run_generic_conflation(
    left_dataset: str,
    right_dataset: str,
    max_distance_meters: float = 500.0,
    time_mode: str = "auto",  # "auto", "window", "overlap", "none"
    time_window_minutes: int = 60,
    generate_map: bool = True,
    limit: int = 5000,
) -> str:
    """
    Generic spatial-temporal conflation between ANY two uploaded datasets.

    This function:
    1. Auto-detects which table each dataset lives in (events or cv_points)
    2. Performs spatial join using ST_DWithin (within X meters)
    3. Optionally performs temporal matching (time window or date range overlap)
    4. Returns matched records with distance and time delta

    Args:
        left_dataset: Name or ID of first dataset (e.g., "crashes", "workzones", "crash_abc123")
        right_dataset: Name or ID of second dataset
        max_distance_meters: Maximum distance for spatial match (default 500m)
        time_mode: "auto" (detect), "window" (±N minutes), "overlap" (date ranges), "none"
        time_window_minutes: For window mode, the ± buffer in minutes
        generate_map: Whether to generate a map of matched records
        limit: Maximum number of matches to return

    Returns:
        Markdown summary with match counts and optional map generation
    """
    log = logging.getLogger("adk_server")
    sid = get_active_session() or "_no_session_"
    uid = (get_active_user() or "dev-user").strip() or "dev-user"

    try:
        left_hb = is_hard_braking_ref(left_dataset)
        right_hb = is_hard_braking_ref(right_dataset)

        # 1. Resolve dataset IDs
        left_id = resolve_dataset_id_generic(left_dataset)
        right_id = resolve_dataset_id_generic(right_dataset)

        # 2. Get dataset info (table, columns, etc.)
        left_info = get_dataset_info(left_id)
        right_info = get_dataset_info(right_id)

        left_info, right_info = _apply_hard_braking_source(
            left_info=left_info,
            right_info=right_info,
            left_hard_braking=left_hb,
            right_hard_braking=right_hb,
        )

        log.info(f"Generic conflation: {left_info.get('name', left_id)} ({left_info['table']}) <-> {right_info.get('name', right_id)} ({right_info['table']})")

        # 3. Determine time matching mode
        time_mode = _normalize_conflation_time_mode(
            time_mode=time_mode,
            left_info=left_info,
            right_info=right_info,
        )

        # 4. Build the SQL query
        left_table = left_info["table"]
        right_table = right_info["table"]
        sql = _build_conflation_sql(
            left_info=left_info,
            right_info=right_info,
            time_mode=time_mode,
            time_window_minutes=time_window_minutes,
        )
        params = _build_conflation_params(
            max_distance_meters=max_distance_meters,
            left_info=left_info,
            right_info=right_info,
            left_table=left_table,
            right_table=right_table,
            owner_user_id=uid,
            limit=limit,
        )

        # 5. Execute the query
        with _db_conn() as conn:
            df = pd.read_sql_query(sql, conn, params=params)

        # 6. Process results
        match_count = len(df)

        # 7. Generate map if requested
        map_point_count = 0
        if generate_map and match_count > 0:
            map_point_count = save_conflation_map(
                match_df=df,
                left_info=left_info,
                right_info=right_info,
                left_id=left_id,
                right_id=right_id,
            )

        # 8. Build response
        return build_conflation_response(
            match_df=df,
            left_info=left_info,
            right_info=right_info,
            left_id=left_id,
            right_id=right_id,
            max_distance_meters=max_distance_meters,
            time_mode=time_mode,
            time_window_minutes=time_window_minutes,
            generate_map=generate_map,
            map_point_count=map_point_count,
        )

    except Exception as e:
        log.error(f"Generic conflation error: {e}", exc_info=True)
        return f"❌ ERROR in conflation: {str(e)}"

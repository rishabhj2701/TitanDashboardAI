"""Catalog and dataset lookup helpers for SQL handlers."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

from ..session_state import get_active_session, get_active_user
from ..storage.postgis.db_pool import get_db_connection
from ..storage.postgis.table_names import APP_DATASET_CODEBOOK, APP_DATASETS, APP_EVENTS, APP_USER_CV_RUN_CONFIG


def _db_conn():
    return get_db_connection()


def _active_user_id() -> str:
    return (get_active_user() or "dev-user").strip() or "dev-user"


def _split_qualified_name(full_name: str) -> tuple[Optional[str], str]:
    if "." in full_name:
        schema, table = full_name.split(".", 1)
        return schema, table
    return None, full_name


def _table_column_names(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        # Use pg_attribute so regular tables, views, and materialized views all resolve.
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass(%s)
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (table_name,),
        )
        cols = {str(r[0]).lower() for r in cur.fetchall()}
        if cols:
            return cols

        # Compatibility fallback for edge cases where to_regclass resolution fails.
        schema, table = _split_qualified_name(table_name)
        if schema:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                """,
                (schema, table),
            )
        else:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name=%s
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                """,
                (table,),
            )
        return {str(r[0]).lower() for r in cur.fetchall()}


def _relation_exists(conn, relation_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (relation_name,))
        row = cur.fetchone()
        return bool(row and row[0])


def _first_existing_relation(conn, candidates: list[str]) -> Optional[str]:
    for name in candidates:
        if _relation_exists(conn, name):
            return name
    return None


_CV_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _active_cv_schema_name(conn) -> Optional[str]:
    try:
        if not _relation_exists(conn, "public.cv_runs"):
            return None
        uid = _active_user_id()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            schema_name = ""
            if uid and (_relation_exists(conn, APP_USER_CV_RUN_CONFIG) or _relation_exists(conn, "public.user_cv_run_config")):
                user_cfg_rel = APP_USER_CV_RUN_CONFIG if _relation_exists(conn, APP_USER_CV_RUN_CONFIG) else "public.user_cv_run_config"
                cur.execute(
                    f"""
                    SELECT r.schema_name
                    FROM {user_cfg_rel} c
                    LEFT JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.user_id = %s
                    LIMIT 1
                    """,
                    (uid,),
                )
                row = cur.fetchone() or {}
                schema_name = str(row.get("schema_name") or "").strip()

            if not schema_name and _relation_exists(conn, "public.cv_run_config"):
                cur.execute(
                    """
                    SELECT r.schema_name
                    FROM public.cv_run_config c
                    LEFT JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.id = 1
                    """
                )
                row = cur.fetchone() or {}
                schema_name = str(row.get("schema_name") or "").strip()
            if not schema_name or not _CV_SCHEMA_NAME_RE.match(schema_name):
                return None
            return schema_name
    except Exception:
        return None


def _cv_relation_candidates(conn, relation_name: str) -> list[str]:
    rel = str(relation_name or "").strip()
    if not rel:
        return []
    candidates: list[str] = []
    active_schema = _active_cv_schema_name(conn)
    if active_schema:
        candidates.append(f"{active_schema}.{rel}")
    candidates.extend([rel, f"public.{rel}"])
    return list(dict.fromkeys(candidates))


def _resolve_point_coord_exprs(conn, table_name: str, alias: str = "p") -> tuple[str, str]:
    cols = _table_column_names(conn, table_name)

    lat_col = next((c for c in ("lat", "latitude") if c in cols), None)
    lon_col = next((c for c in ("lon", "lng", "longitude") if c in cols), None)
    if lat_col and lon_col:
        return f"{alias}.{lat_col}", f"{alias}.{lon_col}"

    geom_col = next((c for c in ("geom", "geometry", "geom_4326") if c in cols), None)
    if geom_col:
        return f"ST_Y({alias}.{geom_col})", f"ST_X({alias}.{geom_col})"

    raise ValueError(
        f"Could not resolve coordinates for table '{table_name}'. "
        f"Expected lat/lon (or latitude/longitude) or a geom column. "
        f"Available columns: {sorted(cols)}"
    )


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
    ref_expr: Optional[str] = None,
    label_expr: Optional[str] = None,
    name_expr: Optional[str] = None,
    highway_expr: Optional[str] = None,
    extra_exprs: Optional[list[str]] = None,
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


def _resolve_traffic_sql_col_map(
    conn,
    hard_brake_only: bool,
    alias: str = "p",
    base_table: Optional[str] = None,
    *,
    sql_col_map: Optional[Dict[str, str]] = None,
    hb_sql_col_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Build a traffic SQL column map that matches the actual source table schema.
    For hard-brake sources, prefer concrete numeric columns when available and
    gracefully fall back to JSON attrs otherwise.
    """
    if sql_col_map is None or hb_sql_col_map is None:
        raise ValueError("sql_col_map and hb_sql_col_map are required")

    table_name = base_table or ("cv_hard_brake_events_mv" if hard_brake_only else "cv_points")
    cols = _table_column_names(conn, table_name)
    col_map = dict(hb_sql_col_map if hard_brake_only else sql_col_map)

    has_attrs = "attrs" in cols
    speed_json_expr = (
        f"COALESCE(NULLIF({alias}.attrs->>'speed','')::float8, NULLIF({alias}.attrs->>'SpeedMPH','')::float8, NULLIF({alias}.attrs->>'speed_mph','')::float8, NULLIF({alias}.attrs->>'speedMPH','')::float8)"
        if has_attrs
        else "NULL::float8"
    )
    speed_limit_json_expr = (
        f"COALESCE(NULLIF({alias}.attrs->>'SpeedLimitMPH','')::float8, NULLIF({alias}.attrs->>'speedLimit','')::float8, NULLIF({alias}.attrs->>'speed_limit_mph','')::float8, NULLIF({alias}.attrs->>'speed_limit','')::float8, NULLIF({alias}.attrs->>'SpeedLimit','')::float8)"
        if has_attrs
        else "NULL::float8"
    )
    acc_x_json_expr = (
        f"CASE WHEN NULLIF({alias}.attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN ({alias}.attrs->>'AccX')::float8 END"
        if has_attrs
        else "NULL::float8"
    )
    acc_y_json_expr = (
        f"CASE WHEN NULLIF({alias}.attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN ({alias}.attrs->>'AccY')::float8 END"
        if has_attrs
        else "NULL::float8"
    )

    speed_expr = f"{alias}.speed" if "speed" in cols else speed_json_expr
    native_speed_limit = next(
        (c for c in ("speed_limit", "speed_limit_mph", "speedlimit") if c in cols),
        None,
    )
    if native_speed_limit:
        speed_limit_expr = f"COALESCE({alias}.{native_speed_limit}, {speed_limit_json_expr})"
    else:
        speed_limit_expr = speed_limit_json_expr

    if "speed_over_limit" in cols:
        speed_over_expr = f"COALESCE({alias}.speed_over_limit, (({speed_expr}) - ({speed_limit_expr})))"
    else:
        speed_over_expr = f"(({speed_expr}) - ({speed_limit_expr}))"

    native_acc_x = next((c for c in ("acc_x", "accx") if c in cols), None)
    native_acc_y = next((c for c in ("acc_y", "accy") if c in cols), None)
    acc_x_expr = f"{alias}.{native_acc_x}" if native_acc_x else acc_x_json_expr
    acc_y_expr = f"{alias}.{native_acc_y}" if native_acc_y else acc_y_json_expr

    native_id_col = next((c for c in ("id", "point_id", "cv_point_id") if c in cols), None)
    id_expr = f"{alias}.{native_id_col}::text" if native_id_col else "NULL::text"

    vehicle_parts: list[str] = []
    for col in ("vehicle_id", "vehicleid", "device_id", "trip_id"):
        if col in cols:
            vehicle_parts.append(f"NULLIF(TRIM({alias}.{col}::text),'')")
    if has_attrs:
        vehicle_parts.extend(
            [
                f"NULLIF(TRIM({alias}.attrs->>'VehicleID'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'vehicle_id'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'vehicleId'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'vehicleid'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'TripID'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'trip_id'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'tripId'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'DeviceID'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'device_id'),'')",
                f"NULLIF(TRIM({alias}.attrs->>'deviceId'),'')",
            ]
        )
    vehicle_id_expr = f"COALESCE({', '.join(vehicle_parts)})" if vehicle_parts else "NULL::text"

    road_id_expr = (
        f"{alias}.road_segment_id"
        if "road_segment_id" in cols
        else (f"{alias}.way_id" if "way_id" in cols else "NULL::text")
    )

    col_map["speed"] = speed_expr
    col_map["speedLimit"] = speed_limit_expr
    col_map["SpeedLimitMPH"] = speed_limit_expr
    col_map["speed_over_limit"] = speed_over_expr
    col_map["acc_x"] = acc_x_expr
    col_map["AccX"] = acc_x_expr
    col_map["acc_y"] = acc_y_expr
    col_map["AccY"] = acc_y_expr
    col_map["id"] = id_expr
    col_map["point_id"] = id_expr
    col_map["vehicle_id"] = vehicle_id_expr
    col_map["VehicleID"] = vehicle_id_expr
    col_map["road_segment_id"] = road_id_expr
    return col_map


def _resolve_cv_enrichment_sql(
    conn,
    base_table: str,
    alias: str = "p",
    *,
    prefer_route_ref: bool = False,
) -> dict[str, Any]:
    """
    Resolve optional joins/expressions that enrich CV points with way/road/speed data.
    """
    base_cols = _table_column_names(conn, base_table)
    joins: list[str] = []

    has_attrs = "attrs" in base_cols
    way_id_expr: Optional[str] = f"{alias}.way_id" if "way_id" in base_cols else None

    match_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_point_match"))
    if not way_id_expr and match_table:
        join_point_col = "id" if "id" in base_cols else ("point_id" if "point_id" in base_cols else None)
        if join_point_col:
            joins.append(f"LEFT JOIN {match_table} m ON m.point_id = {alias}.{join_point_col}")
            way_id_expr = "m.way_id"

    roads_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "osm_roads_match"))
    roads_cols: set[str] = set()
    if roads_table and way_id_expr:
        joins.append(f"LEFT JOIN {roads_table} orm ON orm.way_id = {way_id_expr}")
        roads_cols = _table_column_names(conn, roads_table)

    stats_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_road_stats_mv"))
    stats_cols: set[str] = set()
    if stats_table and way_id_expr:
        joins.append(f"LEFT JOIN {stats_table} rs ON rs.way_id = {way_id_expr}")
        stats_cols = _table_column_names(conn, stats_table)

    road_id_expr = (
        f"{alias}.road_segment_id"
        if "road_segment_id" in base_cols
        else (f"{way_id_expr}::text" if way_id_expr else "NULL::text")
    )

    if prefer_route_ref:
        ref_parts: list[str] = []
        label_parts: list[str] = []
        name_parts: list[str] = []
        highway_parts: list[str] = []

        if has_attrs:
            ref_parts.extend(
                [
                    f"NULLIF({alias}.attrs->>'ref','')",
                    f"NULLIF({alias}.attrs->>'Ref','')",
                    f"NULLIF({alias}.attrs->>'route','')",
                    f"NULLIF({alias}.attrs->>'Route','')",
                    f"NULLIF({alias}.attrs->>'highway_ref','')",
                    f"NULLIF({alias}.attrs->>'highwayRef','')",
                ]
            )
            name_parts.extend(
                [
                    f"NULLIF({alias}.attrs->>'road','')",
                    f"NULLIF({alias}.attrs->>'RoadName','')",
                    f"NULLIF({alias}.attrs->>'roadName','')",
                    f"NULLIF({alias}.attrs->>'road_name','')",
                ]
            )

        for col in ("ref", "route", "highway_ref", "highwayref"):
            if col in base_cols:
                ref_parts.append(f"NULLIF({alias}.{col}::text,'')")
        for col in ("label",):
            if col in base_cols:
                label_parts.append(f"NULLIF({alias}.{col}::text,'')")
        for col in ("road", "road_name", "name"):
            if col in base_cols:
                name_parts.append(f"NULLIF({alias}.{col}::text,'')")
        if "highway" in base_cols:
            highway_parts.append(f"NULLIF(initcap(replace({alias}.highway::text, '_', ' ')),'')")

        if roads_cols:
            for col in ("ref", "route", "highway_ref", "highwayref"):
                if col in roads_cols:
                    ref_parts.append(f"NULLIF(orm.{col}::text,'')")
            if "label" in roads_cols:
                label_parts.append("NULLIF(orm.label::text,'')")
            for col in ("road_name", "name"):
                if col in roads_cols:
                    name_parts.append(f"NULLIF(orm.{col}::text,'')")
            if "highway" in roads_cols:
                highway_parts.append("NULLIF(initcap(replace(orm.highway::text, '_', ' ')),'')")

        if stats_cols:
            for col in ("ref", "route", "highway_ref", "highwayref"):
                if col in stats_cols:
                    ref_parts.append(f"NULLIF(rs.{col}::text,'')")
            if "label" in stats_cols:
                label_parts.append("NULLIF(rs.label::text,'')")
            for col in ("road_name", "name"):
                if col in stats_cols:
                    name_parts.append(f"NULLIF(rs.{col}::text,'')")
            if "highway" in stats_cols:
                highway_parts.append("NULLIF(initcap(replace(rs.highway::text, '_', ' ')),'')")

        ref_expr = f"COALESCE({', '.join(ref_parts)})" if ref_parts else None
        label_expr = f"COALESCE({', '.join(label_parts)})" if label_parts else None
        name_expr = f"COALESCE({', '.join(name_parts)})" if name_parts else None
        highway_expr = f"COALESCE({', '.join(highway_parts)})" if highway_parts else None
        road_name_expr = _preferred_road_name_expr(
            ref_expr=ref_expr,
            label_expr=label_expr,
            name_expr=name_expr,
            highway_expr=highway_expr,
        )
    else:
        road_name_parts: list[str] = []
        if has_attrs:
            road_name_parts.extend(
                [
                    f"NULLIF({alias}.attrs->>'road','')",
                    f"NULLIF({alias}.attrs->>'RoadName','')",
                    f"NULLIF({alias}.attrs->>'roadName','')",
                    f"NULLIF({alias}.attrs->>'road_name','')",
                    f"NULLIF({alias}.attrs->>'ref','')",
                ]
            )
        for col in ("road", "road_name", "name", "label", "ref"):
            if col in base_cols:
                road_name_parts.append(f"NULLIF({alias}.{col}::text,'')")
        if "highway" in base_cols:
            road_name_parts.append(f"NULLIF(initcap(replace({alias}.highway::text, '_', ' ')),'')")

        if roads_cols:
            for col in ("label", "road_name", "name", "ref"):
                if col in roads_cols:
                    road_name_parts.append(f"NULLIF(orm.{col}::text,'')")
            if "highway" in roads_cols:
                road_name_parts.append("NULLIF(initcap(replace(orm.highway::text, '_', ' ')),'')")

        if stats_cols:
            for col in ("label", "road_name", "name", "ref"):
                if col in stats_cols:
                    road_name_parts.append(f"NULLIF(rs.{col}::text,'')")
            if "highway" in stats_cols:
                road_name_parts.append("NULLIF(initcap(replace(rs.highway::text, '_', ' ')),'')")

        road_name_expr = f"COALESCE({', '.join(road_name_parts)})" if road_name_parts else "NULL::text"

    road_ref_parts: list[str] = []
    if has_attrs:
        road_ref_parts.extend(
            [
                f"NULLIF({alias}.attrs->>'ref','')",
                f"NULLIF({alias}.attrs->>'Ref','')",
                f"NULLIF({alias}.attrs->>'route','')",
                f"NULLIF({alias}.attrs->>'Route','')",
                f"NULLIF({alias}.attrs->>'highway_ref','')",
                f"NULLIF({alias}.attrs->>'highwayRef','')",
            ]
        )
    for col in ("ref", "route", "highway_ref", "highwayref"):
        if col in base_cols:
            road_ref_parts.append(f"NULLIF({alias}.{col}::text,'')")
    if roads_cols:
        for col in ("ref", "route", "highway_ref", "highwayref"):
            if col in roads_cols:
                road_ref_parts.append(f"NULLIF(orm.{col}::text,'')")
    if stats_cols:
        for col in ("ref", "route", "highway_ref", "highwayref"):
            if col in stats_cols:
                road_ref_parts.append(f"NULLIF(rs.{col}::text,'')")
    road_ref_expr = f"COALESCE({', '.join(road_ref_parts)})" if road_ref_parts else "NULL::text"

    speed_limit_parts: list[str] = []
    for col in ("speed_limit", "speed_limit_mph", "speedlimit"):
        if col in base_cols:
            speed_limit_parts.append(f"{alias}.{col}::float8")
    if has_attrs:
        speed_limit_parts.append(
            f"COALESCE(NULLIF({alias}.attrs->>'SpeedLimitMPH','')::float8, NULLIF({alias}.attrs->>'speedLimit','')::float8, NULLIF({alias}.attrs->>'speed_limit_mph','')::float8, NULLIF({alias}.attrs->>'speed_limit','')::float8, NULLIF({alias}.attrs->>'SpeedLimit','')::float8)"
        )
    if stats_cols:
        for col in ("speed_limit_mph", "avg_speed_limit_mph"):
            if col in stats_cols:
                speed_limit_parts.append(f"rs.{col}::float8")
    if roads_cols:
        for col in ("speed_limit_mph", "maxspeed_mph"):
            if col in roads_cols:
                speed_limit_parts.append(f"orm.{col}::float8")
    speed_limit_expr = f"COALESCE({', '.join(speed_limit_parts)})" if speed_limit_parts else "NULL::float8"

    return {
        "joins": joins,
        "road_id_expr": road_id_expr,
        "road_name_expr": road_name_expr,
        "road_ref_expr": road_ref_expr,
        "speed_limit_expr": speed_limit_expr,
        "way_id_expr": way_id_expr or "NULL::bigint",
    }


def _latest_cv_dataset_id() -> Optional[str]:
    from ..services.cv import latest_cv_dataset_id

    return latest_cv_dataset_id()


def _resolve_hard_brake_table(conn) -> Optional[str]:
    candidates: list[str] = []
    candidates.extend(_cv_relation_candidates(conn, "cv_hard_brake_events_mv"))
    candidates.extend(_cv_relation_candidates(conn, "cv_hard_brake"))
    return _first_existing_relation(conn, list(dict.fromkeys(candidates)))


def _latest_dataset_id_from_relation(conn, table_name: str) -> Optional[str]:
    cols = _table_column_names(conn, table_name)
    if "dataset_id" not in cols:
        return None

    order_clause = ""
    if "id" in cols:
        order_clause = " ORDER BY id DESC"
    elif "ts" in cols:
        order_clause = " ORDER BY ts DESC NULLS LAST"

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT dataset_id FROM {table_name} WHERE dataset_id IS NOT NULL{order_clause} LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None


def _active_cv_run_context() -> dict[str, Any]:
    try:
        uid = _active_user_id()
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            row = {}
            if uid and (_relation_exists(conn, APP_USER_CV_RUN_CONFIG) or _relation_exists(conn, "public.user_cv_run_config")):
                user_cfg_rel = APP_USER_CV_RUN_CONFIG if _relation_exists(conn, APP_USER_CV_RUN_CONFIG) else "public.user_cv_run_config"
                cur.execute(
                    f"""
                    SELECT
                        c.active_run_id AS run_id,
                        r.schema_name,
                        r.state_code,
                        r.point_count,
                        r.ts_start,
                        r.ts_end
                    FROM {user_cfg_rel} c
                    LEFT JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.user_id = %s
                    LIMIT 1
                    """,
                    (uid,),
                )
                row = cur.fetchone() or {}
            if not row.get("run_id") and _relation_exists(conn, "public.cv_run_config"):
                cur.execute(
                    """
                    SELECT
                        c.active_run_id AS run_id,
                        r.schema_name,
                        r.state_code,
                        r.point_count,
                        r.ts_start,
                        r.ts_end
                    FROM public.cv_run_config c
                    LEFT JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.id = 1
                    """
                )
                row = cur.fetchone() or {}
            run_id = row.get("run_id")
            if not run_id:
                return {}
            return {
                "run_id": run_id,
                "schema_name": row.get("schema_name"),
                "state_code": row.get("state_code"),
                "point_count": row.get("point_count"),
                "ts_start": row.get("ts_start"),
                "ts_end": row.get("ts_end"),
            }
    except Exception:
        return {}


_EVENT_SCHEMA_CACHE: dict[str, set[str]] = {}


def _get_event_schema_columns(dataset_id: str) -> set[str]:
    if not dataset_id:
        return set()
    cached = _EVENT_SCHEMA_CACHE.get(dataset_id)
    if cached is not None:
        return cached
    cols: set[str] = set()
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            uid = _active_user_id()
            cur.execute("SELECT to_regclass(%s)", (APP_EVENTS,))
            if not cur.fetchone()[0]:
                _EVENT_SCHEMA_CACHE[dataset_id] = set()
                return set()
            event_cols = _table_column_names(conn, APP_EVENTS)
            where = "WHERE e.dataset_id = %s"
            params: list[Any] = [dataset_id]
            if "owner_user_id" in event_cols:
                where += " AND e.owner_user_id = %s"
                params.append(uid)
            cur.execute(
                f"""
                SELECT DISTINCT key
                FROM {APP_EVENTS} e,
                LATERAL jsonb_object_keys(e.props) AS key
                {where}
                """,
                params,
            )
            cols = {row[0] for row in cur.fetchall() if row and row[0]}
    except Exception:
        cols = set()
    _EVENT_SCHEMA_CACHE[dataset_id] = cols
    return cols


def _latest_event_dataset_id(entity_type: str = "crash") -> Optional[str]:
    uid = _active_user_id()
    sid = get_active_session()
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            dataset_cols = _table_column_names(conn, APP_DATASETS)
            if "owner_user_id" in dataset_cols:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE owner_user_id=%s AND status='ready' AND entity_type=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uid, entity_type),
                )
            elif sid:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE session_id=%s AND status='ready' AND entity_type=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (sid, entity_type),
                )
            else:
                return None
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _looks_like_dataset_id(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return re.match(r"^[a-zA-Z]+_[0-9a-fA-F]{6,}$", text) is not None


def _resolve_event_dataset_id(value: Optional[str], entity_type: str = "crash") -> Optional[str]:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    uid = _active_user_id()
    sid = get_active_session()
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            dataset_cols = _table_column_names(conn, APP_DATASETS)
            owner_scoped = "owner_user_id" in dataset_cols
            if owner_scoped:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE owner_user_id=%s AND dataset_id=%s AND entity_type=%s
                    LIMIT 1
                    """,
                    (uid, value, entity_type),
                )
            elif sid:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE session_id=%s AND dataset_id=%s AND entity_type=%s
                    LIMIT 1
                    """,
                    (sid, value, entity_type),
                )
            else:
                return value if _looks_like_dataset_id(value) else None
            row = cur.fetchone()
            if row:
                return row[0]

            if owner_scoped:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE owner_user_id=%s AND lower(name)=lower(%s) AND entity_type=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uid, value, entity_type),
                )
            else:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE session_id=%s AND lower(name)=lower(%s) AND entity_type=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (sid, value, entity_type),
                )
            row = cur.fetchone()
            if row:
                return row[0]

            if owner_scoped:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE owner_user_id=%s AND lower(name)=lower(%s)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uid, value),
                )
            else:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE session_id=%s AND lower(name)=lower(%s)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (sid, value),
                )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return value if _looks_like_dataset_id(value) else None


def _lookup_pedestrian_accident_type_codes() -> list[str]:
    """
    Resolve accident_type code values that correspond to pedestrian-involved crashes.
    """
    uid = _active_user_id()
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            table_cols = _table_column_names(conn, APP_DATASET_CODEBOOK)
            if "owner_user_id" in table_cols:
                cur.execute(
                    f"""
                    SELECT DISTINCT code
                    FROM {APP_DATASET_CODEBOOK}
                    WHERE owner_user_id = %s
                      AND label IS NOT NULL
                      AND label ILIKE %s
                      AND (
                        UPPER(attr) LIKE '%%ACCIDENT%%TYPE%%'
                        OR UPPER(attr) LIKE '%%CRASH%%TYPE%%'
                      )
                    ORDER BY code
                    """,
                    (uid, "%pedestrian%"),
                )
            else:
                sid = get_active_session()
                if not sid:
                    return []
                cur.execute(
                    f"""
                    SELECT DISTINCT code
                    FROM {APP_DATASET_CODEBOOK}
                    WHERE session_id = %s
                      AND label IS NOT NULL
                      AND label ILIKE %s
                      AND (
                        UPPER(attr) LIKE '%%ACCIDENT%%TYPE%%'
                        OR UPPER(attr) LIKE '%%CRASH%%TYPE%%'
                      )
                    ORDER BY code
                    """,
                    (sid, "%pedestrian%"),
                )
            raw_codes = [
                str(row[0]).strip()
                for row in cur.fetchall()
                if row and row[0] is not None and str(row[0]).strip()
            ]
            variants: list[str] = []
            seen: set[str] = set()
            for code in raw_codes:
                candidates = [code]
                if re.fullmatch(r"\d+", code):
                    stripped = code.lstrip("0") or "0"
                    candidates.append(stripped)
                for candidate in candidates:
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        variants.append(candidate)
            return variants
    except Exception:
        return []


__all__ = [
    "_active_cv_run_context",
    "_active_cv_schema_name",
    "_cv_relation_candidates",
    "_first_existing_relation",
    "_get_event_schema_columns",
    "_latest_cv_dataset_id",
    "_latest_dataset_id_from_relation",
    "_latest_event_dataset_id",
    "_lookup_pedestrian_accident_type_codes",
    "_looks_like_dataset_id",
    "_relation_exists",
    "_resolve_cv_enrichment_sql",
    "_resolve_event_dataset_id",
    "_resolve_hard_brake_table",
    "_resolve_point_coord_exprs",
    "_resolve_traffic_sql_col_map",
    "_split_qualified_name",
    "_table_column_names",
]

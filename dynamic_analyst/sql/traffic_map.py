"""Traffic SQL map/query helpers."""

from __future__ import annotations

from ..storage.postgis.table_names import APP_EVENTS
from .common import (
    Any,
    CRASH_TIMEZONE,
    Optional,
    RealDictCursor,
    TRAFFIC_MAP_LIMIT_DEFAULT,
    TRAFFIC_MAP_LIMIT_MAX,
    TRAFFIC_MAP_LIMIT_NEAR_MAX,
    TRAFFIC_NEAR_COMBINED_MAX,
    TRAFFIC_NEAR_CRASH_OVERLAY_MAX,
    TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS,
    TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX,
    _db_conn,
    re,
)

def _is_reset_map_request(
    reasoning: str,
    filters: list[dict],
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
    hard_brake_only: bool,
) -> bool:
    return bool(re.search(r"\breset\b", (reasoning or ""), re.IGNORECASE)) and not (
        filters or near_crash or near_workzone or hard_brake_only
    )


def _resolve_traffic_map_sampling(
    map_req: dict,
    *,
    has_near_filter: bool,
) -> tuple[int, float, int]:
    try:
        requested_limit = int(map_req.get("limit", TRAFFIC_MAP_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        requested_limit = TRAFFIC_MAP_LIMIT_DEFAULT
    requested_limit = max(1, requested_limit)
    if has_near_filter:
        limit = min(requested_limit, TRAFFIC_MAP_LIMIT_NEAR_MAX)
    else:
        limit = min(requested_limit, TRAFFIC_MAP_LIMIT_MAX)

    grid_deg = float(map_req.get("grid_deg", 0.0015))
    grid_deg = max(0.0003, min(grid_deg, 0.05))
    per_cell = int(map_req.get("per_cell", 5))
    per_cell = max(1, min(per_cell, 8))
    return limit, grid_deg, per_cell


def _build_traffic_map_sql(
    *,
    from_sql: str,
    where_sql: str,
    lat_expr: str,
    lon_expr: str,
    way_id_expr: str,
    road_segment_id_expr: str,
    road_name_expr: str,
    speed_expr: str,
    speed_limit_expr: str,
    speed_over_limit_expr: str,
    acc_x_expr: str,
    acc_y_expr: str,
    id_expr: str,
    vehicle_id_expr: str,
) -> str:
    return f"""
        WITH filtered AS (
        SELECT
            {lat_expr} AS latitude,
            {lon_expr} AS longitude,
            p.ts AS timestamp,
            {way_id_expr} AS way_id,
            {road_segment_id_expr} AS road_segment_id,
            {road_name_expr} AS road_name,
            {speed_expr} AS speed,
            {speed_limit_expr} AS "speedLimit",
            {speed_over_limit_expr} AS speed_over_limit,
            {acc_x_expr} AS acc_x,
            {acc_y_expr} AS acc_y,

            FLOOR({lat_expr} / %s) AS lat_bin,
            FLOOR({lon_expr} / %s) AS lon_bin,

            md5(
            COALESCE(({id_expr})::text,'') ||
            COALESCE(({vehicle_id_expr})::text,'') ||
            COALESCE(p.ts::text,'') ||
            COALESCE({road_name_expr}::text,'')
            ) AS stable_key
        FROM {from_sql}
        WHERE {where_sql}
            AND {lat_expr} IS NOT NULL
            AND {lon_expr} IS NOT NULL
        ),
        ranked AS (
        SELECT *,
            ROW_NUMBER() OVER (
            PARTITION BY lat_bin, lon_bin
            ORDER BY stable_key
            ) AS rn
        FROM filtered
        )
        SELECT
        latitude, longitude, timestamp, way_id, road_segment_id, road_name,
        speed, "speedLimit", speed_over_limit, acc_x, acc_y
        FROM ranked
        WHERE rn <= %s
        ORDER BY stable_key
        LIMIT %s
    """


def _traffic_map_label(
    *,
    hard_brake_only: bool,
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
) -> str:
    if hard_brake_only and near_crash and near_workzone:
        return "Hard braking + crashes + workzones (near)"
    if hard_brake_only and near_crash:
        return "Hard braking + crashes (near)"
    if hard_brake_only and near_workzone:
        return "Hard braking + workzones (near)"
    if hard_brake_only:
        return "Hard braking events"
    return "Traffic Map"


def _fetch_near_crash_rows(
    *,
    from_sql: str,
    where_sql: str,
    lat_expr: str,
    lon_expr: str,
    where_params: list[Any],
    near_crash: dict,
    crash_dataset_id: str,
    map_limit: int,
) -> list[dict[str, Any]]:
    crash_sql = f"""
        WITH cv AS (
          SELECT {lat_expr} AS lat, {lon_expr} AS lon, p.ts
          FROM {from_sql}
          WHERE {where_sql}
            AND {lat_expr} IS NOT NULL
            AND {lon_expr} IS NOT NULL
            AND p.ts IS NOT NULL
        ),
        crash_raw AS (
          SELECT
            e.id,
            e.lat AS latitude,
            e.lon AS longitude,
            e.ts AS timestamp,
            e.road_segment_id,
            NULLIF(e.props->>'severity','') AS severity,
            COALESCE(NULLIF(e.props->>'hp_acc_image_no',''),NULLIF(e.props->>'accident_id',''),NULLIF(e.props->>'primary_id',''),NULLIF(e.props->>'crash_id',''),NULLIF(e.props->>'id','')) AS primary_id,
            NULLIF(e.props->>'hp_acc_image_no','') AS hp_acc_image_no,
            COALESCE(
              NULLIF(e.props->>'_event_date_norm',''),
              NULLIF(e.props->>'accident_date',''),
              NULLIF(e.props->>'event_date',''),
              NULLIF(e.props->>'crash_date','')
            ) AS source_accident_date,
            COALESCE(
              NULLIF(e.props->>'_event_time_norm',''),
              NULLIF(e.props->>'accident_time',''),
              NULLIF(e.props->>'event_time',''),
              NULLIF(e.props->>'crash_time','')
            ) AS source_accident_time
          FROM {APP_EVENTS} e
          WHERE e.dataset_id = %s
        ),
        crash AS (
          SELECT
            r.id,
            r.latitude,
            r.longitude,
            r.timestamp,
            r.road_segment_id,
            r.severity,
            r.primary_id,
            r.hp_acc_image_no,
            r.source_accident_date,
            r.source_accident_time,
            CASE
              WHEN r.source_accident_date IS NOT NULL
                   AND (
                     NULLIF(SUBSTRING(r.source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL
                     OR NULLIF(SUBSTRING(r.source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL
                   )
                THEN (
                  r.source_accident_date::date
                  + COALESCE(
                      NULLIF(SUBSTRING(r.source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), '')::time,
                      NULLIF(SUBSTRING(r.source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}})'), '')::time
                    )
                ) AT TIME ZONE '{CRASH_TIMEZONE}'
              WHEN r.source_accident_time ~ 'T' THEN r.source_accident_time::timestamptz
              WHEN r.timestamp IS NOT NULL THEN r.timestamp
              ELSE NULL
            END AS crash_ts
          FROM crash_raw r
        )
        SELECT DISTINCT ON (primary_id, latitude, longitude)
          latitude,
          longitude,
          timestamp,
          road_segment_id,
          severity,
          primary_id,
          hp_acc_image_no,
          COALESCE(source_accident_date, TO_CHAR((crash_ts AT TIME ZONE '{CRASH_TIMEZONE}')::date, 'YYYY-MM-DD')) AS accident_date,
          COALESCE(
            NULLIF(SUBSTRING(source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})'), ''),
            CASE
              WHEN NULLIF(SUBSTRING(source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}})'), '') IS NOT NULL
                THEN NULLIF(SUBSTRING(source_accident_time FROM '([0-9]{{2}}:[0-9]{{2}})'), '') || ':00'
              ELSE NULL
            END,
            TO_CHAR((crash_ts AT TIME ZONE '{CRASH_TIMEZONE}')::time, 'HH24:MI:SS')
          ) AS accident_time
        FROM crash c
        JOIN cv p ON c.crash_ts IS NOT NULL
          AND p.ts BETWEEN c.crash_ts - (%s || ' minutes')::interval
                      AND c.crash_ts + (%s || ' minutes')::interval
          AND c.latitude IS NOT NULL
          AND c.longitude IS NOT NULL
          AND ST_DWithin(
            ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography,
            ST_SetSRID(ST_MakePoint(c.longitude, c.latitude), 4326)::geography,
            %s
          )
        LIMIT %s
    """
    crash_limit = min(
        TRAFFIC_NEAR_CRASH_OVERLAY_MAX,
        max(250, map_limit // 2),
    )
    with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS}ms",))
        cur.execute(
            crash_sql,
            where_params
            + [
                crash_dataset_id,
                near_crash.get("window_minutes", 60),
                near_crash.get("window_minutes", 60),
                near_crash.get("distance_m", 200.0),
                crash_limit,
            ],
        )
        return cur.fetchall()


def _fetch_near_workzone_rows(
    *,
    from_sql: str,
    where_sql: str,
    lat_expr: str,
    lon_expr: str,
    where_params: list[Any],
    near_workzone: dict,
    workzone_dataset_id: str,
    map_limit: int,
) -> list[dict[str, Any]]:
    wz_sql = f"""
        WITH cv AS (
          SELECT {lat_expr} AS lat, {lon_expr} AS lon, p.ts
          FROM {from_sql}
          WHERE {where_sql}
            AND {lat_expr} IS NOT NULL
            AND {lon_expr} IS NOT NULL
            AND p.ts IS NOT NULL
        )
        SELECT DISTINCT ON (e.id)
          e.id,
          e.road_segment_id,
          e.lat,
          e.lon,
          e.props->>'start_date' AS start_date,
          e.props->>'end_date' AS end_date,
          e.props->>'geometry' AS geometry,
          e.props->>'core_details' AS core_details,
          e.props->>'vehicle_impact' AS vehicle_impact
        FROM {APP_EVENTS} e
        JOIN cv p ON NULLIF(e.props->>'start_date','') IS NOT NULL
          AND NULLIF(e.props->>'end_date','') IS NOT NULL
          AND p.ts BETWEEN (e.props->>'start_date')::timestamptz AND (e.props->>'end_date')::timestamptz
          AND e.lat IS NOT NULL
          AND e.lon IS NOT NULL
          AND ST_DWithin(
            ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography,
            ST_SetSRID(ST_MakePoint(e.lon, e.lat), 4326)::geography,
            %s
          )
        WHERE e.dataset_id = %s
        LIMIT %s
    """
    workzone_limit = min(
        TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX,
        max(200, map_limit // 3),
    )
    with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_NEAR_OVERLAY_QUERY_TIMEOUT_MS}ms",))
        cur.execute(
            wz_sql,
            where_params + [near_workzone.get("distance_m", 200.0), workzone_dataset_id, workzone_limit],
        )
        return cur.fetchall()


def _cap_combined_near_points(map_payload: dict, map_limit: int) -> None:
    max_combined_points = min(
        TRAFFIC_NEAR_COMBINED_MAX,
        map_limit + TRAFFIC_NEAR_CRASH_OVERLAY_MAX + TRAFFIC_NEAR_WORKZONE_OVERLAY_MAX,
    )
    if len(map_payload.get("points", [])) > max_combined_points:
        map_payload["points"] = map_payload["points"][:max_combined_points]
        map_payload["count"] = len(map_payload["points"])
        map_payload["truncated"] = True

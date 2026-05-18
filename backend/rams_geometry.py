"""RAMS route geometry helpers (crash/CV route_id, no OSM)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _scalar(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def segment_stats_table(cur) -> Optional[str]:
    cur.execute("SELECT to_regclass('public.cv_route_segment_stats')")
    if not _scalar(cur.fetchone()):
        return None
    return "public.cv_route_segment_stats"


def cv_points_row_count(cur, cv_table: str) -> int:
    cur.execute(f"SELECT COUNT(*)::bigint FROM {cv_table}")
    return int(_scalar(cur.fetchone()) or 0)


def use_rams_segment_cv(cur, cv_table: str) -> bool:
    seg = segment_stats_table(cur)
    if not seg:
        return False
    return cv_points_row_count(cur, cv_table) == 0


def nearest_rams_route_id(
    cur,
    crash_lat: float,
    crash_lon: float,
    max_dist_m: float = 75.0,
) -> Tuple[Optional[str], Optional[str]]:
    cur.execute(
        """
        SELECT
            r.road_segment_id,
            COALESCE(NULLIF(r.name, ''), r.road_segment_id) AS road_name
        FROM public.roads r
        WHERE r.geom_m IS NOT NULL
          AND ST_DWithin(
            r.geom_m,
            ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 26915),
            %s
          )
        ORDER BY r.geom_m <-> ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 26915)
        LIMIT 1
        """,
        (crash_lon, crash_lat, max_dist_m, crash_lon, crash_lat),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    if isinstance(row, dict):
        return row.get("road_segment_id"), row.get("road_name")
    return row[0], row[1]


def crash_analysis_segment_summary(cur, params: Dict[str, Any], road_clause: str) -> Dict[str, Any]:
    cur.execute(
        f"""
        WITH crash AS (
          SELECT
            %(road_segment_id)s::text AS road_segment_id,
            CASE
              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
              ELSE NULL
            END AS crash_ts
        ),
        filtered AS (
          SELECT
            s.speed_mean_mph,
            s.journeyid_nunique,
            s.decel_03g_sum
          FROM public.cv_route_segment_stats s
          CROSS JOIN crash c
          WHERE c.crash_ts IS NOT NULL
            AND s.timestamp_5min BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
            {road_clause}
        )
        SELECT
          COUNT(*) AS points,
          COALESCE(SUM(journeyid_nunique), 0) AS vehicles,
          AVG(speed_mean_mph) AS avg_speed,
          NULL::float8 AS avg_speed_limit,
          NULL::float8 AS avg_speed_over_limit
        FROM filtered
        """,
        params,
    )
    row = cur.fetchone() or {}
    return dict(row) if isinstance(row, dict) else {
        "points": row[0],
        "vehicles": row[1],
        "avg_speed": row[2],
        "avg_speed_limit": row[3],
        "avg_speed_over_limit": row[4],
    }


def crash_analysis_segment_braking(cur, params: Dict[str, Any], road_clause: str) -> Dict[str, Any]:
    cur.execute(
        f"""
        WITH crash AS (
          SELECT
            CASE
              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
              ELSE NULL
            END AS crash_ts
        ),
        filtered AS (
          SELECT s.decel_03g_sum, s.journeyid_nunique
          FROM public.cv_route_segment_stats s
          CROSS JOIN crash c
          WHERE c.crash_ts IS NOT NULL
            AND s.timestamp_5min BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
            {road_clause}
        )
        SELECT
          COALESCE(SUM(decel_03g_sum), 0)::bigint AS hard_braking_events,
          COALESCE(SUM(journeyid_nunique), 0)::bigint AS hard_braking_vehicles
        FROM filtered
        """,
        params,
    )
    row = cur.fetchone() or {}
    return dict(row) if isinstance(row, dict) else {
        "hard_braking_events": row[0],
        "hard_braking_vehicles": row[1],
    }


def crash_analysis_segment_buckets(cur, params: Dict[str, Any], road_clause: str) -> list:
    cur.execute(
        f"""
        WITH crash AS (
          SELECT
            CASE
              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
              ELSE NULL
            END AS crash_ts
        ),
        filtered AS (
          SELECT s.timestamp_5min AS ts, s.speed_mean_mph AS speed
          FROM public.cv_route_segment_stats s
          CROSS JOIN crash c
          WHERE c.crash_ts IS NOT NULL
            AND s.timestamp_5min BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
            {road_clause}
        ),
        bucketed AS (
          SELECT
            CASE
              WHEN ts >= crash_ts - interval '60 minutes' AND ts < crash_ts - interval '15 minutes' THEN '1 hr–15 min before'
              WHEN ts >= crash_ts - interval '15 minutes' AND ts < crash_ts - interval '5 minutes' THEN '15–5 min before'
              WHEN ts >= crash_ts - interval '5 minutes' AND ts <= crash_ts THEN '5 min to crash'
              WHEN ts > crash_ts AND ts <= crash_ts + interval '5 minutes' THEN '0–5 min after'
              WHEN ts > crash_ts + interval '5 minutes' AND ts <= crash_ts + interval '15 minutes' THEN '5–15 min after'
              WHEN ts > crash_ts + interval '15 minutes' AND ts <= crash_ts + interval '60 minutes' THEN '15–60 min after'
              ELSE NULL
            END AS bucket,
            speed
          FROM filtered, crash
        )
        SELECT
          bucket,
          AVG(speed) AS avg_speed,
          NULL::float8 AS avg_speed_limit,
          CASE
            WHEN bucket = '1 hr–15 min before' THEN 1
            WHEN bucket = '15–5 min before' THEN 2
            WHEN bucket = '5 min to crash' THEN 3
            WHEN bucket = '0–5 min after' THEN 4
            WHEN bucket = '5–15 min after' THEN 5
            WHEN bucket = '15–60 min after' THEN 6
            ELSE 99
          END AS bucket_order
        FROM bucketed
        WHERE bucket IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket_order
        """,
        params,
    )
    rows = cur.fetchall() or []
    return [dict(r) for r in rows]


def segment_base_counts(cur, params: Dict[str, Any], road_clause: str) -> Dict[str, Any]:
    cur.execute(
        f"""
        WITH crash AS (
          SELECT
            %(road_segment_id)s::text AS road_segment_id,
            CASE
              WHEN %(crash_ts)s IS NOT NULL THEN %(crash_ts)s::timestamptz
              WHEN %(accident_date)s IS NOT NULL AND %(accident_time)s IS NOT NULL
                THEN ((%(accident_date)s::date + %(accident_time)s::time) AT TIME ZONE %(crash_tz)s)
              ELSE NULL
            END AS crash_ts
        ),
        base AS (
          SELECT s.route_id::text AS route_id
          FROM public.cv_route_segment_stats s
          CROSS JOIN crash c
          WHERE c.crash_ts IS NOT NULL
            AND s.timestamp_5min BETWEEN c.crash_ts - (%(window_minutes)s || ' minutes')::interval
                                    AND c.crash_ts + (%(window_minutes)s || ' minutes')::interval
            {road_clause}
        )
        SELECT
          COUNT(*) AS base_points,
          COUNT(*) FILTER (WHERE route_id = (SELECT road_segment_id FROM crash)) AS same_road_points
        FROM base
        """,
        params,
    )
    row = cur.fetchone() or {}
    return dict(row) if isinstance(row, dict) else {"base_points": row[0], "same_road_points": row[1]}


def route_line_geojson(cur, route_id: str) -> Optional[dict]:
    if not route_id:
        return None
    cur.execute(
        """
        SELECT ST_AsGeoJSON(geom)::json
        FROM public.roads
        WHERE road_segment_id = %s AND geom IS NOT NULL
        LIMIT 1
        """,
        (route_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    geom = row.get("st_asgeojson") if isinstance(row, dict) else row[0]
    if not geom:
        return None
    return {
        "type": "Feature",
        "properties": {"road_segment_id": route_id, "label": route_id},
        "geometry": geom,
    }

-- Shared definition for public.cv_road_stats_mv (no placeholder speed limits).
DROP MATERIALIZED VIEW IF EXISTS public.cv_road_stats_mv;

CREATE MATERIALIZED VIEW public.cv_road_stats_mv AS
SELECT
    s.route_id AS way_id,
    s.route_id AS road_segment_id,
    s.route_id AS label,
    COALESCE(r.name, s.route_id) AS name,
    r.geom AS geom_4326,
    ST_Transform(r.geom, 3857) AS geom_3857,
    SUM(s.journeyid_count) AS point_count,
    AVG(s.speed_mean_mph) AS avg_speed_mph,
    AVG(s.speed_mean_mph) AS p50_speed_mph,
    MAX(s.speed_max_mph) AS p90_speed_mph,
    NULL::float8 AS speed_limit_mph,
    NULL::float8 AS speed_limit_p50_mph,
    MIN(s.timestamp_5min) AS start_ts,
    MAX(s.timestamp_5min) AS end_ts,
    SUM(s.journeyid_nunique) AS unique_vehicles_total,
    SUM(s.decel_03g_sum) AS hard_brake_count
FROM public.cv_route_segment_stats s
LEFT JOIN public.roads r
    ON NULLIF(TRIM(r.road_segment_id::text), '') = NULLIF(TRIM(s.route_id::text), '')
GROUP BY s.route_id, r.name, r.geom;

CREATE UNIQUE INDEX IF NOT EXISTS cv_road_stats_mv_way_id_idx
    ON public.cv_road_stats_mv (way_id);

ANALYZE public.cv_road_stats_mv;

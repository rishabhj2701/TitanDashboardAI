-- Table to store 5-minute binned Connected Vehicle segment aggregates
CREATE TABLE IF NOT EXISTS public.cv_route_segment_stats (
    id SERIAL PRIMARY KEY,
    route_id text NOT NULL,
    segment_start_measure double precision NOT NULL,
    timestamp_5min timestamp with time zone NOT NULL,
    journeyid_count integer,
    journeyid_nunique integer,
    speed_min_mph double precision,
    speed_max_mph double precision,
    speed_mean_mph double precision,
    speed_std_mph double precision,
    speed_q85_mph double precision,
    speed_q15_mph double precision,
    speed_min_kmph double precision,
    speed_max_kmph double precision,
    speed_mean_kmph double precision,
    acceleration_min double precision,
    acceleration_max double precision,
    acceleration_mean double precision,
    acceleration_std double precision,
    distance_from_route_min double precision,
    distance_from_route_max double precision,
    distance_from_route_mean double precision,
    distance_from_route_std double precision,
    acc_01g_sum integer,
    acc_02g_sum integer,
    acc_03g_sum integer,
    acc_04g_sum integer,
    acc_05g_sum integer,
    acc_075g_sum integer,
    acc_maxg_sum integer,
    decel_01g_sum integer,
    decel_02g_sum integer,
    decel_03g_sum integer,
    decel_04g_sum integer,
    decel_05g_sum integer,
    decel_075g_sum integer,
    decel_maxg_sum integer,
    overspeed_5mph integer,
    overspeed_10mph integer,
    overspeed_15mph integer,
    overspeed_20mph integer,
    overspeed_25mph integer,
    year integer,
    month integer,
    day integer,
    hour integer
);

-- Optimize queries by binned segment and time
CREATE INDEX IF NOT EXISTS idx_cv_rss_route_measure 
    ON public.cv_route_segment_stats (route_id, segment_start_measure);
CREATE INDEX IF NOT EXISTS idx_cv_rss_ts 
    ON public.cv_route_segment_stats (timestamp_5min);

-- Create CV Road Stats Materialized View for Mapbox rendering
CREATE MATERIALIZED VIEW IF NOT EXISTS public.cv_road_stats_mv AS
SELECT
    s.route_id AS way_id, -- Compatible with existing code
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

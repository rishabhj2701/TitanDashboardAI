-- Rebuild road labels/refs and MV metrics for CV road tiles.
-- Run against DB `traffic` in container `postgis_conflate`.

-- 1) Enrich osm_roads_match with `ref` and a stable display `label`.
ALTER TABLE public.osm_roads_match ADD COLUMN IF NOT EXISTS ref text;
ALTER TABLE public.osm_roads_match ADD COLUMN IF NOT EXISTS label text;

UPDATE public.osm_roads_match r
SET
  name = COALESCE(NULLIF(r.name, ''), NULLIF(l.name, '')),
  ref = COALESCE(NULLIF(r.ref, ''), NULLIF(split_part(l.ref, ';', 1), '')),
  highway = COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')),
  label = COALESCE(
    NULLIF(r.label, ''),
    NULLIF(split_part(l.ref, ';', 1), ''),
    NULLIF(COALESCE(NULLIF(r.name, ''), NULLIF(l.name, '')), ''),
    CASE
      WHEN COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')) IS NOT NULL
        THEN initcap(replace(COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')), '_', ' ')) || ' #' || r.way_id::text
      ELSE 'Way ' || r.way_id::text
    END
  )
FROM public.planet_osm_line l
WHERE l.osm_id = r.way_id;

UPDATE public.osm_roads_match r
SET label = COALESCE(
  NULLIF(r.label, ''),
  NULLIF(r.ref, ''),
  NULLIF(r.name, ''),
  CASE
    WHEN NULLIF(r.highway, '') IS NOT NULL
      THEN initcap(replace(r.highway, '_', ' ')) || ' #' || r.way_id::text
    ELSE 'Way ' || r.way_id::text
  END
)
WHERE r.label IS NULL OR r.label = '';

CREATE INDEX IF NOT EXISTS osm_roads_match_ref_idx ON public.osm_roads_match (ref);
CREATE INDEX IF NOT EXISTS osm_roads_match_label_idx ON public.osm_roads_match (label);
ANALYZE public.osm_roads_match;

-- 2) Rebuild MV with speed-limit metrics and improved labels.
DROP MATERIALIZED VIEW IF EXISTS public.cv_road_stats_mv;

CREATE MATERIALIZED VIEW public.cv_road_stats_mv AS
WITH matched AS (
  SELECT
    m.way_id,
    p.ts,
    p.vehicle_id,
    p.speed,
    m.dist_m,
    COALESCE(
      NULLIF(p.attrs->>'speed_limit_mph','')::float8,
      NULLIF(p.attrs->>'speed_limit','')::float8,
      NULLIF(p.attrs->>'speedlimit_mph','')::float8,
      NULLIF(p.attrs->>'SpeedLimitMPH','')::float8,
      NULLIF(p.attrs->>'speedLimit','')::float8,
      NULLIF(p.attrs->>'SpeedLimit','')::float8
    ) AS speed_limit_mph
  FROM public.cv_point_match m
  JOIN public.cv_points p ON p.id = m.point_id
)
SELECT
  r.way_id,
  r.name,
  r.ref,
  r.label,
  r.highway,
  r.geom_3857,
  r.geom_4326,
  COUNT(*)::bigint AS point_count,
  COUNT(DISTINCT NULLIF(m.vehicle_id::text, ''))::bigint AS unique_vehicles_total,
  CASE
    WHEN COUNT(DISTINCT date_trunc('hour', m.ts)) > 0
      THEN COUNT(DISTINCT NULLIF(m.vehicle_id::text, ''))::float8
        / COUNT(DISTINCT date_trunc('hour', m.ts))::float8
    ELSE NULL::float8
  END AS avg_unique_vehicles_per_hour,
  AVG(m.speed)::float8 AS avg_speed_mph,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY m.speed) AS p50_speed_mph,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY m.speed) AS p90_speed_mph,
  AVG(m.speed_limit_mph)::float8 AS speed_limit_mph,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY m.speed_limit_mph)
    FILTER (WHERE m.speed_limit_mph IS NOT NULL) AS speed_limit_p50_mph,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY m.speed_limit_mph)
    FILTER (WHERE m.speed_limit_mph IS NOT NULL) AS speed_limit_p90_mph,
  MIN(m.ts) AS start_ts,
  MAX(m.ts) AS end_ts,
  AVG(m.dist_m)::float8 AS avg_match_dist_m,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY m.dist_m) AS p90_match_dist_m
FROM matched m
JOIN public.osm_roads_match r ON r.way_id = m.way_id
GROUP BY r.way_id, r.name, r.ref, r.label, r.highway, r.geom_3857, r.geom_4326
WITH NO DATA;

CREATE UNIQUE INDEX cv_road_stats_mv_way_id_idx ON public.cv_road_stats_mv (way_id);
CREATE INDEX cv_road_stats_mv_geom3857_gist ON public.cv_road_stats_mv USING gist (geom_3857);
CREATE INDEX cv_road_stats_mv_point_count_idx ON public.cv_road_stats_mv (point_count);
CREATE INDEX cv_road_stats_mv_label_idx ON public.cv_road_stats_mv (label);

REFRESH MATERIALIZED VIEW public.cv_road_stats_mv;
ANALYZE public.cv_road_stats_mv;

-- 3) Tile function: emit label/ref/highway + speed-limit fields.
CREATE OR REPLACE FUNCTION public.get_cv_roads_mvt(
  z int,
  x int,
  y int,
  min_points int DEFAULT 20
)
RETURNS bytea
LANGUAGE sql
STABLE
AS $$
WITH bounds AS (
  SELECT ST_TileEnvelope(z, x, y) AS geom
),
mvtgeom AS (
  SELECT
    way_id,
    COALESCE(NULLIF(label,''), NULLIF(ref,''), NULLIF(name,''),
      CASE
        WHEN NULLIF(highway,'') IS NOT NULL
          THEN initcap(replace(highway, '_', ' ')) || ' #' || way_id::text
        ELSE 'Way ' || way_id::text
      END
    ) AS road_name,
    name,
    ref,
    label,
    highway,
    point_count,
    unique_vehicles_total,
    avg_unique_vehicles_per_hour,
    avg_speed_mph,
    p50_speed_mph,
    p90_speed_mph,
    speed_limit_mph,
    speed_limit_p50_mph,
    speed_limit_p90_mph,
    avg_match_dist_m,
    p90_match_dist_m,
    ST_AsMVTGeom(
      CASE
        WHEN z <= 7 THEN ST_Simplify(geom_3857, 200)
        WHEN z <= 9 THEN ST_Simplify(geom_3857, 80)
        WHEN z <= 11 THEN ST_Simplify(geom_3857, 30)
        ELSE geom_3857
      END,
      bounds.geom, 4096, 64, true
    ) AS geom
  FROM public.cv_road_stats_mv, bounds
  WHERE geom_3857 && bounds.geom
    AND point_count >= min_points
)
SELECT ST_AsMVT(mvtgeom, 'roads', 4096, 'geom')
FROM mvtgeom;
$$;

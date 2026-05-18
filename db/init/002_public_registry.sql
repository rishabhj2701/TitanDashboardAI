-- Public registry tables for the OSM road network and the CV run selector.
-- The conflation pipeline (scripts/cv_conflation_pipeline.py) populates these
-- and creates per-run schemas with CV points, matches, and aggregate views.

-- Matchable road table. One row per OSM way; populated by the pipeline from
-- public.planet_osm_line after osm2pgsql import.
CREATE TABLE IF NOT EXISTS public.osm_roads_match (
    way_id    bigint PRIMARY KEY,
    name      text,
    highway   text,
    geom_4326 geometry(MultiLineString, 4326),
    geom_3857 geometry(MultiLineString, 3857),
    ref       text,
    label     text
);

CREATE INDEX IF NOT EXISTS osm_roads_match_geom3857_gist
    ON public.osm_roads_match USING gist (geom_3857);
CREATE INDEX IF NOT EXISTS osm_roads_match_ref_idx
    ON public.osm_roads_match (ref);
CREATE INDEX IF NOT EXISTS osm_roads_match_label_idx
    ON public.osm_roads_match (label);

-- Registry of available CV datasets. Each row points at a per-run schema
-- (e.g. cv_mo_202512_winter) created by the pipeline.
CREATE TABLE IF NOT EXISTS public.cv_runs (
    run_id              text PRIMARY KEY,
    schema_name         text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    display_name        text,
    description         text,
    season_tag          text,
    state_code          text,
    road_count          bigint,
    point_count         bigint,
    ts_start            timestamptz,
    ts_end              timestamptz,
    bbox_4326           geometry(Polygon, 4326),
    stats_refreshed_at  timestamptz,
    is_visible          boolean NOT NULL DEFAULT true,
    sort_order          integer NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cv_runs_visible_sort
    ON public.cv_runs (is_visible, sort_order DESC, created_at DESC);

-- Global active-run pointer used by the public tile dispatch function.
CREATE TABLE IF NOT EXISTS public.cv_run_config (
    id              integer PRIMARY KEY,
    active_run_id   text REFERENCES public.cv_runs(run_id)
);

-- Public tile dispatch. Reads cv_run_config.active_run_id, looks up the run
-- schema in cv_runs, and forwards the tile request to that schema's
-- get_cv_roads_mvt(). Per-run functions are created by the pipeline script.
CREATE OR REPLACE FUNCTION public.get_cv_roads_mvt(
    z integer, x integer, y integer, min_points integer DEFAULT 20
) RETURNS bytea
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    s text;
    dynsql text;
    out_bytes bytea;
BEGIN
    SELECT r.schema_name INTO s
    FROM public.cv_run_config c
    JOIN public.cv_runs r ON r.run_id = c.active_run_id
    WHERE c.id = 1;

    IF s IS NULL THEN
        RAISE EXCEPTION 'No active CV run set. Update public.cv_run_config.active_run_id';
    END IF;

    dynsql := format('SELECT %I.get_cv_roads_mvt($1,$2,$3,$4)', s);
    EXECUTE dynsql INTO out_bytes USING z, x, y, min_points;
    RETURN out_bytes;
END;
$$;

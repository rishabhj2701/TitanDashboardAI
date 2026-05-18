#!/usr/bin/env bash
# Import Iowa OSM extract and build road match tables for map tiles.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PBF="${1:-$ROOT/iowa-260516.osm.pbf}"
DB="${OSM_DB:-traffic}"
HOST="${OSM_HOST:-localhost}"
PORT="${OSM_PORT:-5432}"
USER="${OSM_USER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-postgres}"

if [[ ! -f "$PBF" ]]; then
  echo "ERROR: OSM file not found: $PBF" >&2
  exit 1
fi

if ! command -v osm2pgsql >/dev/null 2>&1; then
  echo "ERROR: osm2pgsql not found (brew install osm2pgsql)" >&2
  exit 1
fi

echo "==> Importing OSM into $DB ($PBF)"
osm2pgsql --create --slim --hstore --multi-geometry \
  --database "$DB" --username "$USER" --host "$HOST" --port "$PORT" \
  "$PBF"

echo "==> Building public.osm_roads and public.osm_roads_match"
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" <<'SQL'
DROP TABLE IF EXISTS public.osm_roads CASCADE;
CREATE TABLE public.osm_roads (
  way_id bigint PRIMARY KEY,
  name text,
  highway text,
  geom_4326 geometry(MultiLineString, 4326),
  geom_3857 geometry(MultiLineString, 3857)
);

INSERT INTO public.osm_roads (way_id, name, highway, geom_4326, geom_3857)
SELECT
  osm_id::bigint AS way_id,
  name,
  highway,
  ST_Transform(ST_Multi(way), 4326) AS geom_4326,
  ST_Multi(way) AS geom_3857
FROM (
  SELECT DISTINCT ON (osm_id)
    osm_id, name, highway, way
  FROM public.planet_osm_line
  WHERE highway IS NOT NULL
    AND highway NOT IN ('cycleway', 'footway', 'path', 'steps', 'pedestrian')
  ORDER BY osm_id, ST_Length(way) DESC NULLS LAST
) q;

CREATE INDEX IF NOT EXISTS osm_roads_geom3857_gist ON public.osm_roads USING GIST (geom_3857);
ANALYZE public.osm_roads;

DROP TABLE IF EXISTS public.osm_roads_match CASCADE;
CREATE TABLE public.osm_roads_match AS
SELECT way_id, name, highway, geom_4326, geom_3857,
       NULL::text AS ref, NULL::text AS label
FROM public.osm_roads;

ALTER TABLE public.osm_roads_match ADD PRIMARY KEY (way_id);
CREATE INDEX IF NOT EXISTS osm_roads_match_geom3857_gist ON public.osm_roads_match USING GIST (geom_3857);

UPDATE public.osm_roads_match r
SET
  name = COALESCE(NULLIF(r.name, ''), NULLIF(l.name, '')),
  ref = COALESCE(NULLIF(r.ref, ''), NULLIF(split_part(l.ref, ';', 1), '')),
  highway = COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')),
  label = COALESCE(
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

ANALYZE public.osm_roads_match;
SQL

echo "==> Done. Refresh CV materialized view if you have segment stats:"
echo "    REFRESH MATERIALIZED VIEW public.cv_road_stats_mv;"

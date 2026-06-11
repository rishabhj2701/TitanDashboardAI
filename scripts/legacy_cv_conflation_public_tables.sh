#!/usr/bin/env bash
set -euo pipefail

# Legacy CV/OSM conflation flow.
#
# This is the older remote/Jupyter-era approach. It writes directly into public
# tables (`cv_points_raw`, `cv_points`, `cv_point_match`) instead of creating
# isolated per-run schemas and `public.cv_runs` metadata. Keep this as a
# reference for how the first PostGIS road-matching setup was built.
#
# Prefer scripts/cv_conflation_pipeline.py for the current app.

OSM_PBF="${OSM_PBF:-$HOME/conflate_data/missouri-260205.osm.pbf}"
CV_CSV_GZ="${CV_CSV_GZ:-$HOME/conflate_data/winter_cv_data_dec_2025.csv.gz}"
CONTAINER="${CONTAINER:-postgis_conflate}"
DB="${DB:-traffic}"
DBUSER="${DBUSER:-postgres}"
OSM_PGHOST="${OSM_PGHOST:-127.0.0.1}"
OSM_PGPORT="${OSM_PGPORT:-5434}"
MATCH_RADIUS_M="${MATCH_RADIUS_M:-20}"
MATCH_WORKERS="${MATCH_WORKERS:-4}"
GEOM_WORKERS="${GEOM_WORKERS:-2}"
DROP_HIGHWAY_SERVICE="${DROP_HIGHWAY_SERVICE:-1}"
RECREATE_ALL="${RECREATE_ALL:-1}"

LOG="legacy_cv_conflation_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[$(ts)] ERROR: missing command: $1"
    exit 1
  }
}

psqlc() {
  sudo docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DB" -v ON_ERROR_STOP=1 -c "$1"
}

psqlat() {
  sudo docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DB" -v ON_ERROR_STOP=1 -Atc "$1"
}

psqlstdin() {
  sudo docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DB" -v ON_ERROR_STOP=1
}

step() {
  local name="$1"
  shift
  echo "[$(ts)] === START: $name ==="
  local t0
  local t1
  t0="$(date +%s)"
  "$@"
  t1="$(date +%s)"
  echo "[$(ts)] === END: $name (elapsed $((t1 - t0))s) ==="
  echo
}

echo "[$(ts)] Logging to: $LOG"
echo "[$(ts)] OSM_PBF=$OSM_PBF"
echo "[$(ts)] CV_CSV_GZ=$CV_CSV_GZ"
echo "[$(ts)] CONTAINER=$CONTAINER DB=$DB DBUSER=$DBUSER"

need_cmd sudo
need_cmd docker
need_cmd gunzip
need_cmd date
need_cmd xargs
need_cmd python3
need_cmd osm2pgsql

[[ -f "$OSM_PBF" ]] || { echo "[$(ts)] ERROR: OSM_PBF not found: $OSM_PBF"; exit 1; }
[[ -f "$CV_CSV_GZ" ]] || { echo "[$(ts)] ERROR: CV_CSV_GZ not found: $CV_CSV_GZ"; exit 1; }

step "Wait for Postgres ready" bash -lc "
until sudo docker exec \"$CONTAINER\" pg_isready -U \"$DBUSER\" -d \"$DB\" >/dev/null 2>&1; do
  sleep 1
done
echo '[OK] pg_isready'
"

step "Create extensions" bash -lc "
psqlc \"CREATE EXTENSION IF NOT EXISTS postgis;\"
psqlc \"CREATE EXTENSION IF NOT EXISTS hstore;\"
"

step "OSM import via osm2pgsql" bash -lc "
osm2pgsql \
  -H \"$OSM_PGHOST\" -P \"$OSM_PGPORT\" -U \"$DBUSER\" -d \"$DB\" \
  --slim --cache 8000 --hstore \
  \"$OSM_PBF\"
"

step "Build osm_roads and osm_roads_match" bash -lc "
psqlstdin <<SQL
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
FROM public.planet_osm_roads
WHERE way IS NOT NULL
  AND highway IS NOT NULL;

CREATE INDEX osm_roads_geom3857_gist ON public.osm_roads USING GIST (geom_3857);
ANALYZE public.osm_roads;

DROP TABLE IF EXISTS public.osm_roads_match CASCADE;
CREATE TABLE public.osm_roads_match AS
SELECT *
FROM public.osm_roads
WHERE highway IS NOT NULL
  AND (CASE WHEN ${DROP_HIGHWAY_SERVICE}::int = 1 THEN highway <> 'service' ELSE true END);

ALTER TABLE public.osm_roads_match ADD PRIMARY KEY (way_id);
CREATE INDEX osm_roads_match_geom3857_gist ON public.osm_roads_match USING GIST (geom_3857);
ANALYZE public.osm_roads_match;
SQL
"

step "Create cv_points_raw" bash -lc "
if [ \"$RECREATE_ALL\" -eq 1 ]; then
  psqlstdin <<SQL
DROP TABLE IF EXISTS public.cv_points_raw CASCADE;
CREATE UNLOGGED TABLE public.cv_points_raw (
  tripid text,
  vehicleid text,
  vehicletype text,
  transporttype text,
  starttime text,
  endtime text,
  edgeid text,
  osmid text,
  longitude text,
  latitude text,
  snappedlongitude text,
  snappedlatitude text,
  timestamputc text,
  speedmph text,
  bearing text,
  accx text,
  accy text,
  length text,
  speedlimitmph text,
  beginheading text,
  roadclass text,
  traversability text,
  \"use\" text,
  surface text,
  lanecount text,
  toll text,
  unpaved text,
  tunnel text,
  bridge text,
  roundabout text,
  internalintersection text,
  truckroute text,
  shoulder text,
  labels text,
  roadname text
);
SQL
fi
"

step "COPY csv.gz to cv_points_raw" bash -lc "
gunzip -c \"$CV_CSV_GZ\" | sudo docker exec -i \"$CONTAINER\" psql -U \"$DBUSER\" -d \"$DB\" -v ON_ERROR_STOP=1 \
  -c \"\\copy public.cv_points_raw FROM STDIN WITH (FORMAT csv, HEADER true)\"
psqlc \"SELECT COUNT(*) AS n_raw FROM public.cv_points_raw;\"
"

step "Compute timestamp bounds" bash -lc "
MIN_TS=\$(psqlat \"SELECT MIN((timestamputc)::timestamptz) FROM public.cv_points_raw WHERE timestamputc IS NOT NULL;\")
MAX_TS=\$(psqlat \"SELECT MAX((timestamputc)::timestamptz) FROM public.cv_points_raw WHERE timestamputc IS NOT NULL;\")
echo \"MIN_TS=\$MIN_TS\"
echo \"MAX_TS=\$MAX_TS\"
echo \"\$MIN_TS\" > .min_ts
echo \"\$MAX_TS\" > .max_ts
"

MIN_TS="$(cat .min_ts)"
MAX_TS="$(cat .max_ts)"
MIN_DAY="$(date -u -d "${MIN_TS}" +%Y-%m-%d)"
MAX_DAY="$(date -u -d "${MAX_TS}" +%Y-%m-%d)"

step "Create partitioned cv_points" bash -lc "
if [ \"$RECREATE_ALL\" -eq 1 ]; then
  psqlstdin <<SQL
DROP TABLE IF EXISTS public.cv_points CASCADE;
CREATE TABLE public.cv_points (
  id bigserial,
  ts timestamptz NOT NULL,
  lon double precision NOT NULL,
  lat double precision NOT NULL,
  speed double precision,
  attrs jsonb,
  geom_4326 geometry(Point, 4326),
  geom_3857 geometry(Point, 3857),
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
CREATE TABLE public.cv_points_default PARTITION OF public.cv_points DEFAULT;
SQL
fi

d=\"$MIN_DAY\"
end=\"$MAX_DAY\"
while [ \"\$d\" != \"\$(date -u -d \"\$end + 1 day\" +%Y-%m-%d)\" ]; do
  next=\$(date -u -d \"\$d + 1 day\" +%Y-%m-%d)
  suf=\$(echo \"\$d\" | tr -d '-')
  psqlc \"CREATE TABLE IF NOT EXISTS public.cv_points_\${suf} PARTITION OF public.cv_points FOR VALUES FROM ('\$d'::timestamptz) TO ('\$next'::timestamptz);\"
  d=\"\$next\"
done
"

step "Insert typed cv_points" bash -lc "
if [ \"$RECREATE_ALL\" -eq 1 ]; then
  psqlc \"TRUNCATE public.cv_points;\"
fi
psqlstdin <<SQL
BEGIN;
SET LOCAL synchronous_commit = off;
SET LOCAL work_mem = '256MB';
INSERT INTO public.cv_points (ts, lon, lat, speed, attrs)
SELECT
  (timestamputc)::timestamptz AS ts,
  COALESCE(NULLIF(snappedlongitude,''), NULLIF(longitude,''))::double precision AS lon,
  COALESCE(NULLIF(snappedlatitude,''), NULLIF(latitude,''))::double precision AS lat,
  NULLIF(speedmph,'')::double precision AS speed,
  to_jsonb(r) AS attrs
FROM public.cv_points_raw r
WHERE timestamputc IS NOT NULL
  AND COALESCE(NULLIF(snappedlongitude,''), NULLIF(longitude,'')) IS NOT NULL
  AND COALESCE(NULLIF(snappedlatitude,''), NULLIF(latitude,'')) IS NOT NULL;
COMMIT;
SQL
"

step "Build geometries" bash -lc "
PARTS=\$(psqlat \"
  SELECT inhrelid::regclass::text
  FROM pg_inherits
  WHERE inhparent='public.cv_points'::regclass
    AND inhrelid::regclass::text LIKE 'cv_points_%'
    AND inhrelid::regclass::text <> 'cv_points_default'
  ORDER BY 1;\")

echo \"\$PARTS\" | xargs -n 1 -P \"$GEOM_WORKERS\" -I {} bash -lc '
  part=\"{}\"
  sudo docker exec -i \"$CONTAINER\" psql -U \"$DBUSER\" -d \"$DB\" -v ON_ERROR_STOP=1 -c \"
    UPDATE \$part
    SET geom_4326 = ST_SetSRID(ST_MakePoint(lon, lat), 4326),
        geom_3857 = ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 3857)
    WHERE geom_4326 IS NULL;\"
'
"

step "Index cv_points" bash -lc "
psqlstdin <<SQL
CREATE INDEX IF NOT EXISTS cv_points_ts_brin ON public.cv_points USING BRIN (ts);
CREATE INDEX IF NOT EXISTS cv_points_geom3857_gist ON public.cv_points USING GIST (geom_3857);
ANALYZE public.cv_points;
SQL
"

step "Create and fill cv_point_match" bash -lc "
psqlstdin <<SQL
DROP TABLE IF EXISTS public.cv_point_match CASCADE;
CREATE TABLE public.cv_point_match (
  point_id bigint PRIMARY KEY,
  way_id bigint NOT NULL,
  dist_m double precision
);
CREATE INDEX IF NOT EXISTS cv_point_match_way_id_idx ON public.cv_point_match (way_id);
SQL

PARTS=\$(psqlat \"
  SELECT inhrelid::regclass::text
  FROM pg_inherits
  WHERE inhparent='public.cv_points'::regclass
    AND inhrelid::regclass::text LIKE 'cv_points_%'
    AND inhrelid::regclass::text <> 'cv_points_default'
  ORDER BY 1;\")

echo \"\$PARTS\" | xargs -n 1 -P \"$MATCH_WORKERS\" -I {} bash -lc '
  part=\"{}\"
  sudo docker exec -i \"$CONTAINER\" psql -U \"$DBUSER\" -d \"$DB\" -v ON_ERROR_STOP=1 -c \"
    INSERT INTO public.cv_point_match (point_id, way_id, dist_m)
    SELECT p.id, r.way_id, ST_Distance(p.geom_3857, r.geom_3857)
    FROM ONLY \$part p
    JOIN LATERAL (
      SELECT way_id, geom_3857
      FROM public.osm_roads_match r
      WHERE ST_DWithin(p.geom_3857, r.geom_3857, $MATCH_RADIUS_M)
      ORDER BY p.geom_3857 <-> r.geom_3857
      LIMIT 1
    ) r ON true
    ON CONFLICT (point_id) DO NOTHING;\"
'
ANALYZE public.cv_point_match;
"

step "Coverage report" bash -lc "
psqlc \"
SELECT
  (SELECT COUNT(*) FROM public.cv_points) AS total_points,
  (SELECT COUNT(*) FROM public.cv_point_match) AS matched_points,
  ROUND(100.0 * (SELECT COUNT(*) FROM public.cv_point_match) / NULLIF((SELECT COUNT(*) FROM public.cv_points),0), 2) AS pct_matched;\"
"

echo "[$(ts)] DONE. Log: $LOG"

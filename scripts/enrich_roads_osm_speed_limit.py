#!/usr/bin/env python3
"""Attach OSM maxspeed (mph) to public.roads via spatial match with planet_osm_line."""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import psycopg2

POSTGIS_DSN = os.environ.get(
    "POSTGIS_DSN",
    "dbname=traffic user=postgres password=postgres host=localhost port=5432",
)
OSM_SQL_PATH = Path(__file__).resolve().parent.parent / "db" / "sql" / "osm_maxspeed.sql"
MV_SQL_PATH = Path(__file__).resolve().parent.parent / "db" / "sql" / "cv_road_stats_mv_def.sql"
MATCH_TOLERANCE_M = float(os.environ.get("OSM_SPEED_LIMIT_NEAR_M", "75"))


SETUP_OSM_TABLE_SQL = """
DROP TABLE IF EXISTS public.osm_line_maxspeed;
CREATE TABLE public.osm_line_maxspeed AS
SELECT
  l.osm_id,
  ST_Transform(l.way, 26915) AS geom_m,
  public.parse_osm_maxspeed_mph(l.tags->'maxspeed') AS mph
FROM public.planet_osm_line l
WHERE l.tags ? 'maxspeed'
  AND l.way IS NOT NULL
  AND l.highway IS NOT NULL
  AND l.highway NOT IN ('cycleway', 'footway', 'path', 'steps', 'pedestrian', 'corridor')
  AND public.parse_osm_maxspeed_mph(l.tags->'maxspeed') IS NOT NULL;

CREATE INDEX osm_line_maxspeed_geom_gist ON public.osm_line_maxspeed USING gist (geom_m);
ANALYZE public.osm_line_maxspeed;
"""


ENRICH_SQL = f"""
UPDATE public.roads r
SET attrs = COALESCE(r.attrs, '{{}}'::jsonb) || jsonb_build_object(
      'speed_limit_mph', ROUND(pick.mph::numeric, 1),
      'speed_limit_source', 'osm_maxspeed',
      'osm_maxspeed_osm_id', pick.osm_id
    )
FROM (
  SELECT DISTINCT ON (r.road_segment_id)
    r.road_segment_id,
    o.osm_id,
    o.mph
  FROM public.roads r
  JOIN LATERAL (
    SELECT o.osm_id, o.mph
    FROM public.osm_line_maxspeed o
    WHERE ST_DWithin(r.geom_m, o.geom_m, {MATCH_TOLERANCE_M})
    ORDER BY r.geom_m <-> o.geom_m
    LIMIT 1
  ) o ON TRUE
  WHERE r.geom_m IS NOT NULL
) pick
WHERE r.road_segment_id = pick.road_segment_id;
"""


def main() -> int:
    with psycopg2.connect(POSTGIS_DSN) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '1800000'")  # 30 min
            cur.execute("SELECT to_regclass('public.planet_osm_line')")
            if not cur.fetchone()[0]:
                print("ERROR: planet_osm_line missing — run scripts/import_iowa_osm.sh first", file=sys.stderr)
                return 1
            cur.execute("SELECT to_regclass('public.roads')")
            if not cur.fetchone()[0]:
                print("ERROR: public.roads missing — run scripts/build_rams_roads.py first", file=sys.stderr)
                return 1

            if OSM_SQL_PATH.is_file():
                cur.execute(OSM_SQL_PATH.read_text())

            print("Building indexed OSM maxspeed lookup table...", flush=True)
            cur.execute(SETUP_OSM_TABLE_SQL)
            cur.execute("SELECT COUNT(*) FROM public.osm_line_maxspeed")
            osm_n = cur.fetchone()[0]
            print(f"  {osm_n:,} OSM ways with parsed maxspeed", flush=True)

            cur.execute(
                "UPDATE public.roads SET attrs = attrs - 'speed_limit_mph' "
                "- 'speed_limit_source' - 'osm_maxspeed_segments' - 'osm_maxspeed_osm_id'"
            )
            print("Matching RAMS routes to nearest OSM segment (KNN within tolerance)...", flush=True)
            cur.execute(ENRICH_SQL)
            enriched = cur.rowcount

            print("Refreshing cv_road_stats_mv...", flush=True)
            if MV_SQL_PATH.is_file():
                cur.execute(MV_SQL_PATH.read_text())

            cur.execute(
                """
                SELECT
                  COUNT(*) AS roads,
                  COUNT(*) FILTER (WHERE NULLIF(attrs->>'speed_limit_mph', '') IS NOT NULL) AS with_limit
                FROM public.roads
                """
            )
            roads_n, with_limit = cur.fetchone()
            cur.execute(
                """
                SELECT
                  COUNT(*) AS mv_routes,
                  COUNT(*) FILTER (WHERE speed_limit_mph IS NOT NULL) AS mv_with_limit
                FROM public.cv_road_stats_mv
                """
            )
            mv_n, mv_limit = cur.fetchone()
            conn.commit()

    print(f"✓ Enriched {enriched:,} RAMS roads with OSM maxspeed")
    print(f"  public.roads: {with_limit:,} / {roads_n:,} have speed_limit_mph")
    print(f"  cv_road_stats_mv: {mv_limit:,} / {mv_n:,} routes expose speed_limit_mph")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build public.roads line geometry from Iowa RAMS route IDs using crash point sequences."""
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
CRASH_DATASET_PATTERN = os.environ.get("RAMS_CRASH_DATASET_PATTERN", "iowa_crash%")
MIN_POINTS_PER_ROUTE = int(os.environ.get("RAMS_MIN_POINTS_PER_ROUTE", "2"))


BUILD_ROADS_SQL = """
-- One MultiLineString per RAMS route_id from ordered crash mileposts (no OSM required).
INSERT INTO public.roads (road_segment_id, name, geom, geom_m, attrs)
SELECT
    route_id,
    route_id AS name,
    ST_Multi(ST_SetSRID(geom, 4326)) AS geom,
    ST_Multi(ST_Transform(ST_SetSRID(geom, 4326), 26915)) AS geom_m,
    jsonb_build_object('source', 'rams_crash_centroid', 'crash_points', pt_count) AS attrs
FROM (
    SELECT
        route_id,
        COUNT(*) AS pt_count,
        CASE
            WHEN COUNT(*) >= 2 THEN ST_MakeLine(geom ORDER BY milepost, lon, lat)
            ELSE ST_Buffer(ST_MakePoint(MAX(lon), MAX(lat)), 0.00015)
        END AS geom
    FROM (
        SELECT
            NULLIF(TRIM(COALESCE(NULLIF(e.road_segment_id, ''), e.props->>'ROUTEID')), '') AS route_id,
            e.lat::float8 AS lat,
            e.lon::float8 AS lon,
            COALESCE(
                NULLIF(e.props->>'MEASURE', '')::float8,
                NULLIF(e.props->>'measure', '')::float8,
                ROW_NUMBER() OVER (
                    PARTITION BY NULLIF(TRIM(COALESCE(NULLIF(e.road_segment_id, ''), e.props->>'ROUTEID')), '')
                    ORDER BY e.ts NULLS LAST
                )::float8
            ) AS milepost,
            ST_SetSRID(ST_MakePoint(e.lon::float8, e.lat::float8), 4326) AS geom
        FROM app_data.events e
        WHERE e.dataset_id LIKE %(crash_pattern)s
          AND e.lat IS NOT NULL
          AND e.lon IS NOT NULL
          AND NULLIF(TRIM(COALESCE(NULLIF(e.road_segment_id, ''), e.props->>'ROUTEID')), '') IS NOT NULL
    ) pts
    WHERE route_id IS NOT NULL
    GROUP BY route_id
    HAVING COUNT(*) >= %(min_points)s
) built;
"""


MV_SQL_PATH = Path(__file__).resolve().parent.parent / "db" / "sql" / "cv_road_stats_mv_def.sql"


def main() -> int:
    with psycopg2.connect(POSTGIS_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.roads')")
        if not cur.fetchone()[0]:
            print("ERROR: public.roads table missing", file=sys.stderr)
            return 1
        cur.execute("SELECT to_regclass('public.cv_route_segment_stats')")
        if not cur.fetchone()[0]:
            print("ERROR: cv_route_segment_stats missing", file=sys.stderr)
            return 1

        cur.execute("TRUNCATE public.roads")
        cur.execute(
            BUILD_ROADS_SQL,
            {"crash_pattern": CRASH_DATASET_PATTERN, "min_points": MIN_POINTS_PER_ROUTE},
        )
        cur.execute("SELECT COUNT(*)::bigint FROM public.roads")
        roads_n = int(cur.fetchone()[0])
        conn.commit()

        print(f"✓ Inserted {roads_n:,} RAMS routes into public.roads")

        if MV_SQL_PATH.is_file():
            cur.execute(MV_SQL_PATH.read_text())
        else:
            cur.execute("REFRESH MATERIALIZED VIEW public.cv_road_stats_mv")
        conn.commit()
        cur.execute(
            """
            SELECT
              COUNT(*) AS routes,
              COUNT(*) FILTER (WHERE geom_4326 IS NOT NULL) AS with_geom
            FROM public.cv_road_stats_mv
            """
        )
        row = cur.fetchone()
        print(f"✓ Refreshed cv_road_stats_mv: {row[0]} routes, {row[1]} with geometry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

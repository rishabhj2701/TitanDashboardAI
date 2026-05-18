#!/usr/bin/env python3
"""Recreate cv_road_stats_mv without placeholder speed limits."""
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
SQL_PATH = Path(__file__).resolve().parent.parent / "db" / "sql" / "cv_road_stats_mv_def.sql"


def main() -> int:
    if not SQL_PATH.is_file():
        print(f"ERROR: missing {SQL_PATH}", file=sys.stderr)
        return 1
    sql = SQL_PATH.read_text()
    with psycopg2.connect(POSTGIS_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.cv_route_segment_stats')")
        if not cur.fetchone()[0]:
            print("ERROR: cv_route_segment_stats missing", file=sys.stderr)
            return 1
        cur.execute(sql)
        cur.execute(
            """
            SELECT
              COUNT(*) AS routes,
              COUNT(*) FILTER (WHERE speed_limit_mph IS NOT NULL) AS with_limit,
              COUNT(*) FILTER (WHERE geom_4326 IS NOT NULL) AS with_geom
            FROM public.cv_road_stats_mv
            """
        )
        routes, with_limit, with_geom = cur.fetchone()
        conn.commit()
    print(f"✓ Recreated cv_road_stats_mv: {routes:,} routes, {with_geom:,} with geometry")
    print(f"  Roads with speed_limit_mph: {with_limit:,} (expect 0 for Iowa segment aggregates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Backfill cv_runs.point_count and ts_start/ts_end from cv_route_segment_stats probe data."""
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
RUN_ID = os.environ.get("CV_RUN_ID", "iowa_cv_winter_2025")


def main() -> int:
    with psycopg2.connect(POSTGIS_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.cv_route_segment_stats')")
        if not cur.fetchone()[0]:
            print("ERROR: cv_route_segment_stats missing", file=sys.stderr)
            return 1
        cur.execute(
            """
            SELECT
                COUNT(*)::bigint,
                COALESCE(SUM(journeyid_count), 0)::bigint,
                MIN(timestamp_5min),
                MAX(timestamp_5min)
            FROM public.cv_route_segment_stats
            """
        )
        probe_bins, probe_count, ts_start, ts_end = cur.fetchone()
        cur.execute(
            """
            UPDATE public.cv_runs
            SET point_count = %s,
                ts_start = %s,
                ts_end = %s,
                stats_refreshed_at = now()
            WHERE run_id = %s
            """,
            (probe_count, ts_start, ts_end, RUN_ID),
        )
        if cur.rowcount == 0:
            print(f"WARNING: no cv_runs row for run_id={RUN_ID}", file=sys.stderr)
            return 1
        cur.execute(
            """
            UPDATE app_data.datasets
            SET stats = COALESCE(stats, '{}'::jsonb) || jsonb_build_object(
                'probe_count', %s::bigint,
                'probe_bins', %s::bigint,
                'point_count', %s::bigint,
                'ts_start', %s::text,
                'ts_end', %s::text
            )
            WHERE dataset_id = %s
            """,
            (
                probe_count,
                probe_bins,
                probe_count,
                ts_start.isoformat() if ts_start else None,
                ts_end.isoformat() if ts_end else None,
                RUN_ID,
            ),
        )
        conn.commit()
    print(f"✓ {RUN_ID}: {probe_count:,} vehicle probes in {probe_bins:,} bins")
    print(f"  Collection window: {ts_start} – {ts_end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

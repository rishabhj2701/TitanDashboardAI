#!/usr/bin/env python3
"""
scripts/import_iowa_cv_segments.py
──────────────────────────────────
High-performance binned importer for Iowa Connected Vehicle (CV) segment aggregates
into the TitanDashboardAI PostgreSQL database.

Usage:
    python scripts/import_iowa_cv_segments.py [--csv PATH] [--session SESSION_ID] [--chunk CHUNK_SIZE]

Requirements: pip install psycopg2-binary pandas python-dotenv tqdm
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import pandas as pd
import psycopg2

# ── Config ───────────────────────────────────────────────────────────────────
POSTGIS_DSN = os.environ.get("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=localhost port=5432")
DEFAULT_CSV = "/Users/rj/TitanDashboardAI/uploads/5ed70d6a-d45a-41c5-b48b-46a777d23efc.csv"
DEFAULT_SESSION = "iowa_cv_session"
DEFAULT_CHUNK = 150000  # High-speed chunk size for copy streaming
OWNER_USER_ID = "dev-user"

# Exact order of columns in CSV matching the database table columns (excluding id)
CSV_COLUMNS = [
    "route_id", "segment_start_measure", "timestamp_5min", "journeyid_count", "journeyid_nunique",
    "speed_min_mph", "speed_max_mph", "speed_mean_mph", "speed_std_mph", "speed_q85_mph", "speed_q15_mph",
    "speed_min_kmph", "speed_max_kmph", "speed_mean_kmph", "acceleration_min", "acceleration_max",
    "acceleration_mean", "acceleration_std", "distance_from_route_min", "distance_from_route_max",
    "distance_from_route_mean", "distance_from_route_std", "acc_01g_sum", "acc_02g_sum", "acc_03g_sum",
    "acc_04g_sum", "acc_05g_sum", "acc_075g_sum", "acc_maxg_sum", "decel_01g_sum", "decel_02g_sum",
    "decel_03g_sum", "decel_04g_sum", "decel_05g_sum", "decel_075g_sum", "decel_maxg_sum", "overspeed_5mph",
    "overspeed_10mph", "overspeed_15mph", "overspeed_20mph", "overspeed_25mph", "year", "month", "day", "hour"
]


def create_dataset(conn, dataset_id: str, session_id: str, csv_path: str) -> None:
    """Register the dataset under app_data.datasets."""
    mapping = {
        "entity_type": "cv",
        "fields": {
            "route_id": "route_id",
            "measure": "segment_start_measure",
            "timestamp": "timestamp_5min",
            "avg_speed": "speed_mean_mph",
            "vehicle_count": "journeyid_nunique",
            "hard_brake_count": "decel_03g_sum"
        }
    }
    stats = {
        "source_file": Path(csv_path).name,
        "import_method": "import_iowa_cv_segments.py",
        "entity_type": "cv",
        "queryable_fields": {
            "fields": [
                {"query_name": "route_id", "source_column": "route_id", "enabled": True},
                {"query_name": "routeid", "source_column": "route_id", "enabled": True},
                {"query_name": "road_segment_id", "source_column": "route_id", "enabled": True},
                {"query_name": "timestamp_5min", "source_column": "timestamp_5min", "enabled": True},
                {"query_name": "timestamp", "source_column": "timestamp_5min", "enabled": True},
                {"query_name": "start_ts", "source_column": "timestamp_5min", "enabled": True},
                {"query_name": "hour", "source_column": "hour", "enabled": True},
                {"query_name": "year", "source_column": "year", "enabled": True},
                {"query_name": "month", "source_column": "month", "enabled": True},
                {"query_name": "day", "source_column": "day", "enabled": True},
                {"query_name": "speed_mean_mph", "source_column": "speed_mean_mph", "enabled": True},
                {"query_name": "journeyid_nunique", "source_column": "journeyid_nunique", "enabled": True},
                {"query_name": "decel_03g_sum", "source_column": "decel_03g_sum", "enabled": True},
                {"query_name": "hard_brake_count", "source_column": "decel_03g_sum", "enabled": True},
            ]
        }
    }
    with conn.cursor() as cur:
        # We also need to add a registry row in public.cv_runs so the tile dispatch knows about this run
        cur.execute("""
            INSERT INTO public.cv_runs
                (run_id, schema_name, display_name, description, season_tag, state_code, is_visible)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description;
        """, (
            dataset_id,
            "public",  # we store in public schema
            "Iowa Connected Vehicle Speed Aggregates",
            f"Segment aggregates binned at 5-minute intervals from {Path(csv_path).name}",
            "winter",
            "IA",
            True
        ))

        # Set active run in public.cv_run_config
        cur.execute("""
            INSERT INTO public.cv_run_config (id, active_run_id)
            VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET active_run_id = EXCLUDED.active_run_id;
        """, (dataset_id,))

        # Create active user CV configuration
        cur.execute("SELECT to_regclass('app_data.user_cv_run_config')")
        if cur.fetchone()[0]:
            cur.execute("""
                INSERT INTO app_data.user_cv_run_config (user_id, active_run_id, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET active_run_id = EXCLUDED.active_run_id, updated_at = now();
            """, (OWNER_USER_ID, dataset_id))

        # Create app_data.datasets record
        cur.execute("""
            INSERT INTO app_data.datasets
                (dataset_id, owner_user_id, session_id, user_id, name, entity_type, status, mapping, stats)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id) DO UPDATE SET
                name = EXCLUDED.name,
                status = 'ready',
                mapping = EXCLUDED.mapping,
                stats = EXCLUDED.stats;
        """, (
            dataset_id,
            OWNER_USER_ID,
            session_id,
            OWNER_USER_ID,
            "Iowa Connected Vehicle Speed Aggregates",
            "cv",
            "ready",
            json.dumps(mapping),
            json.dumps(stats),
        ))
    conn.commit()
    print(f"✓ Dataset registered: {dataset_id}")


INT_COLUMNS = [
    "journeyid_count", "journeyid_nunique", "acc_01g_sum", "acc_02g_sum", "acc_03g_sum",
    "acc_04g_sum", "acc_05g_sum", "acc_075g_sum", "acc_maxg_sum", "decel_01g_sum",
    "decel_02g_sum", "decel_03g_sum", "decel_04g_sum", "decel_05g_sum", "decel_075g_sum",
    "decel_maxg_sum", "overspeed_5mph", "overspeed_10mph", "overspeed_15mph",
    "overspeed_20mph", "overspeed_25mph", "year", "month", "day", "hour"
]


def copy_df_chunk(conn, df: pd.DataFrame) -> int:
    """Stream a dataframe chunk directly to Postgres using COPY FROM."""
    # Filter out rows with missing critical identifiers
    df = df.dropna(subset=["route_id", "segment_start_measure", "timestamp_5min"])
    if df.empty:
        return 0

    # Convert integer columns to pandas nullable Int64 to avoid float formatting (e.g. 1.0)
    for col in INT_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

    # Write to a string buffer in tab-separated format
    output = io.StringIO()
    # Normalize NA values to \N which COPY understands
    df.to_csv(output, sep='\t', header=False, index=False, na_rep='\\N')
    output.seek(0)

    with conn.cursor() as cur:
        cur.copy_from(output, 'cv_route_segment_stats', sep='\t', null='\\N', columns=CSV_COLUMNS)
    conn.commit()
    return len(df)


def refresh_materialized_view(conn, dataset_id: str, row_count: int) -> None:
    """Refresh the cv_road_stats_mv materialized view and update stats."""
    t_start = time.perf_counter()
    print("Refreshing public.cv_road_stats_mv materialized view (this links aggregates to spatial road geometries)...", end=" ", flush=True)
    with conn.cursor() as cur:
        mv_sql_path = Path(__file__).resolve().parent.parent / "db" / "sql" / "cv_road_stats_mv_def.sql"
        if mv_sql_path.is_file():
            cur.execute(mv_sql_path.read_text())
        else:
            cur.execute("REFRESH MATERIALIZED VIEW public.cv_road_stats_mv;")

        cur.execute("SELECT count(*) FROM public.cv_road_stats_mv;")
        road_count = int(cur.fetchone()[0] or 0)

        # Probe stats from segment table: vehicle readings + collection window (not import date).
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

        cur.execute("""
            UPDATE app_data.datasets
            SET stats = stats || jsonb_build_object(
                'row_count', %s::bigint,
                'road_count', %s::bigint,
                'point_count', %s::bigint,
                'probe_count', %s::bigint,
                'probe_bins', %s::bigint,
                'ts_start', %s::text,
                'ts_end', %s::text,
                'imported_at', now()::text
            )
            WHERE dataset_id = %s;
        """, (
            row_count,
            road_count,
            probe_count,
            probe_count,
            probe_bins,
            ts_start.isoformat() if ts_start else None,
            ts_end.isoformat() if ts_end else None,
            dataset_id,
        ))

        cur.execute("""
            UPDATE public.cv_runs
            SET road_count = %s,
                point_count = %s,
                ts_start = %s,
                ts_end = %s,
                stats_refreshed_at = now()
            WHERE run_id = %s;
        """, (road_count, probe_count, ts_start, ts_end, dataset_id))
    conn.commit()
    duration = time.perf_counter() - t_start
    print(f"OK ({duration:.2f}s)")
    print(
        f"  {road_count:,} roads | {probe_count:,} vehicle probes "
        f"({probe_bins:,} five-minute bins) | {ts_start} – {ts_end}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="High-performance Iowa Connected Vehicle Segment Aggregates Importer")
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"Path to CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--session", default=DEFAULT_SESSION, help=f"Session ID (default: {DEFAULT_SESSION})")
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help=f"Rows per copy batch (default: {DEFAULT_CHUNK})")
    parser.add_argument("--dataset-id", default=None, help="Override dataset ID")
    args = parser.parse_args()

    csv_path = args.csv
    if not Path(csv_path).exists():
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    # Use a fixed short dataset ID/run ID representing Connected Vehicle aggregates
    dataset_id = args.dataset_id or "iowa_cv_winter_2025"
    session_id = args.session

    print("=" * 60)
    print("🚀 Connected Vehicle (CV) Segment Aggregates Importer")
    print(f"  CSV File:   {csv_path}")
    print(f"  Dataset ID: {dataset_id}")
    print(f"  Session ID: {session_id}")
    print(f"  Chunk Size: {args.chunk:,}")
    print(f"  Database:   {POSTGIS_DSN}")
    print("=" * 60)

    # Connect
    print("Connecting to database...", end=" ", flush=True)
    conn = psycopg2.connect(POSTGIS_DSN)
    print("OK")

    # Clear old raw stats before re-importing
    print("Cleaning up old route segment aggregates...", end=" ", flush=True)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE public.cv_route_segment_stats RESTART IDENTITY CASCADE;")
    conn.commit()
    print("OK")

    # Register
    create_dataset(conn, dataset_id, session_id, csv_path)

    # Ingest in high-speed chunks
    print("\nStreaming segment aggregates via PostgreSQL COPY...")
    t_start = time.perf_counter()
    imported = 0
    chunk_num = 0

    # Read CSV in chunks
    for chunk_df in pd.read_csv(csv_path, chunksize=args.chunk, low_memory=False):
        chunk_num += 1
        t_chunk = time.perf_counter()
        
        # Ensure correct column ordering matching database schema
        chunk_df = chunk_df[CSV_COLUMNS]
        
        # Stream chunk
        n = copy_df_chunk(conn, chunk_df)
        imported += n
        
        chunk_dur = time.perf_counter() - t_chunk
        print(f"  Chunk {chunk_num:3d} | Ingested {imported:>10,} rows | Chunk Time: {chunk_dur:.2f}s | Speed: {n/chunk_dur:>9,.0f} rows/sec")

    total_dur = time.perf_counter() - t_start
    print(f"\n✓ Raw Ingestion complete! streamed {imported:,} rows in {total_dur:.2f}s (Avg speed: {imported/total_dur:,.0f} rows/sec)")

    # Materialized View compile
    refresh_materialized_view(conn, dataset_id, imported)
    
    conn.close()
    print("\n" + "=" * 60)
    print("✓ Ingestion and geometry mapping completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()

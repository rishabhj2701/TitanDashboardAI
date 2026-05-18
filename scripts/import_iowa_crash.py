#!/usr/bin/env python3
"""
scripts/import_iowa_crash.py
────────────────────────────
One-shot importer for the Iowa crash CSV into the TitanDashboardAI PostGIS database.

Usage:
    python scripts/import_iowa_crash.py [--csv PATH] [--session SESSION_ID] [--chunk CHUNK_SIZE]

Defaults:
    --csv      crash_data.csv  (project root)
    --session  iowa_crash_session
    --chunk    5000            (rows per DB batch)

What it does:
  1. Connects to PostGIS using POSTGIS_DSN from .env
  2. Ensures app_data schema and tables exist (runs schema migration)
  3. Creates a dataset record in app_data.datasets
  4. Reads the Iowa CSV in chunks, normalising columns:
       CRASH_KEY  → props + primary_id alias
       LATITUDE   → lat
       LONGITUDE  → lon
       ROUTEID    → road_segment_id
       CSEVERITY  → severity prop (kept raw; codebook translates at query time)
       CRASH_DATE → ts  (YYYYMMDD integer → date, combined with TIMESTR for datetime)
       CRASHMONTH / CRASH_DAY / CRASH_YEAR / TIMESTR / MEASURE / COUNTY → props
  5. Bulk-inserts into app_data.events (skips road matching — no OSM needed)
  6. Updates dataset row_count in app_data.datasets.stats

Requirements: pip install psycopg2-binary pandas python-dotenv tqdm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on exported env vars

import pandas as pd
import psycopg2
import psycopg2.extras

# ── Config ───────────────────────────────────────────────────────────────────
POSTGIS_DSN = os.environ.get("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=localhost port=5432")
DEFAULT_CSV = str(Path(__file__).resolve().parent.parent / "crash_data.csv")
DEFAULT_SESSION = "iowa_crash_session"
DEFAULT_CHUNK = 5000
OWNER_USER_ID = "dev-user"  # No auth — fixed dev user

# Iowa KABCO severity labels
SEVERITY_LABELS = {
    "1": "Fatal",
    "2": "Major Injury",
    "3": "Minor Injury",
    "4": "Property Damage Only",
    "5": "Unknown / Not Reported",
}

# Month names for display
MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def _parse_iowa_timestamp(row: pd.Series) -> datetime | None:
    """Parse CRASH_DATE (YYYYMMDD int) + TIMESTR (HH:MM) into a UTC datetime."""
    try:
        crash_date = int(row.get("CRASH_DATE") or 0)
        if crash_date < 19000101:
            return None
        year = crash_date // 10000
        month = (crash_date % 10000) // 100
        day = crash_date % 100
        hour, minute = 0, 0
        timestr = str(row.get("TIMESTR") or "").strip()
        if ":" in timestr:
            parts = timestr.split(":")
            hour = int(parts[0]) if parts[0].isdigit() else 0
            minute = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)
    except Exception:
        return None


def _build_props(row: pd.Series) -> dict:
    """Build the props JSON from an Iowa crash row."""
    props: dict = {}
    for col in row.index:
        val = row[col]
        if pd.isna(val):
            continue
        # Store everything as-is in props for full queryability
        props[col] = val if not isinstance(val, float) else (None if pd.isna(val) else val)
    # Normalise severity label for display
    sev_raw = str(row.get("CSEVERITY") or "").strip()
    if sev_raw:
        props["severity"] = sev_raw
        props["severity_label"] = SEVERITY_LABELS.get(sev_raw, sev_raw)
    # Normalised date/time strings for crash domain queries
    crash_date = int(row.get("CRASH_DATE") or 0)
    if crash_date >= 19000101:
        year = crash_date // 10000
        month = (crash_date % 10000) // 100
        day = crash_date % 100
        props["_event_date_norm"] = f"{year:04d}-{month:02d}-{day:02d}"
    timestr = str(row.get("TIMESTR") or "").strip()
    if timestr:
        props["_event_time_norm"] = timestr
    return props


def ensure_schema(conn) -> None:
    """Ensure app_data schema and tables exist."""
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS app_data;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_data.datasets (
                dataset_id      text PRIMARY KEY,
                owner_user_id   text,
                session_id      text NOT NULL,
                user_id         text,
                name            text NOT NULL,
                entity_type     text,
                status          text NOT NULL DEFAULT 'ready',
                mapping         jsonb DEFAULT '{}'::jsonb,
                provenance      jsonb DEFAULT '[]'::jsonb,
                stats           jsonb DEFAULT '{}'::jsonb,
                created_at      timestamptz NOT NULL DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_data.events (
                id              bigserial PRIMARY KEY,
                dataset_id      text NOT NULL,
                owner_user_id   text,
                session_id      text NOT NULL,
                user_id         text,
                ts              timestamptz,
                lat             double precision,
                lon             double precision,
                geom            geometry(Point, 4326),
                geom_m          geometry(Point, 26915),
                geom_feature    geometry(Geometry, 4326),
                geom_feature_m  geometry(Geometry, 26915),
                road_segment_id text,
                way_id          bigint,
                road_dist_m     double precision,
                road_conf       double precision,
                props           jsonb NOT NULL DEFAULT '{}'::jsonb
            );
        """)
        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS events_dataset_idx ON app_data.events (dataset_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS events_ts_idx ON app_data.events (ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS events_owner_idx ON app_data.events (owner_user_id, session_id);")
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS events_geom_idx ON app_data.events USING gist (geom);")
        except Exception:
            pass  # PostGIS extension may not be enabled yet
    conn.commit()
    print("✓ Schema ensured (app_data.datasets + app_data.events)")


def create_dataset(conn, dataset_id: str, session_id: str, csv_path: str) -> None:
    """Insert a dataset record."""
    mapping = {
        "entity_type": "crash",
        "fields": {
            "primary_id": "CRASH_KEY",
            "event_date": "CRASH_DATE",
            "event_time": "TIMESTR",
            "latitude": "LATITUDE",
            "longitude": "LONGITUDE",
            "road_id": "ROUTEID",
        },
    }
    stats = {
        "source_file": Path(csv_path).name,
        "import_method": "import_iowa_crash.py",
        "iowa_columns": ["CRASH_KEY", "LATITUDE", "LONGITUDE", "ROUTEID", "MEASURE",
                         "CSEVERITY", "CRASH_DATE", "CRASHMONTH", "CRASH_DAY",
                         "CRASH_YEAR", "TIMESTR", "COUNTY"],
        "queryable_fields": {
            "fields": [
                {"query_name": "routeid", "source_column": "ROUTEID", "enabled": True},
                {"query_name": "road_segment_id", "source_column": "road_segment_id", "enabled": True},
                {"query_name": "road_name", "source_column": "road_segment_id", "enabled": True},
                {"query_name": "severity", "source_column": "CSEVERITY", "enabled": True},
                {"query_name": "cseverity", "source_column": "CSEVERITY", "enabled": True},
                {"query_name": "county", "source_column": "COUNTY", "enabled": True},
                {"query_name": "year", "source_column": "CRASH_YEAR", "enabled": True},
                {"query_name": "month", "source_column": "CRASHMONTH", "enabled": True},
                {"query_name": "day_of_week", "source_column": "CRASH_DAY", "enabled": True},
                {"query_name": "milepost", "source_column": "MEASURE", "enabled": True},
                {"query_name": "crash_time", "source_column": "TIMESTR", "enabled": True},
                {"query_name": "local_hour", "source_column": "local_hour", "enabled": True},
                {"query_name": "primary_id", "source_column": "CRASH_KEY", "enabled": True},
                {"query_name": "event_date", "source_column": "event_date", "enabled": True},
                {"query_name": "latitude", "source_column": "latitude", "enabled": True},
                {"query_name": "longitude", "source_column": "longitude", "enabled": True},
            ]
        }
    }
    with conn.cursor() as cur:
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
            "Iowa Crash Data",
            "crash",
            "ready",
            json.dumps(mapping),
            json.dumps(stats),
        ))
    conn.commit()
    print(f"✓ Dataset record created: {dataset_id}")


def import_chunk(conn, rows: list[dict], dataset_id: str, session_id: str) -> int:
    """Bulk-insert a chunk of event rows. Returns number inserted."""
    if not rows:
        return 0

    records = []
    for r in rows:
        ts = r.get("ts")
        lat = r.get("lat")
        lon = r.get("lon")
        road_segment_id = r.get("road_segment_id")
        props = r.get("props", {})

        # Build geometry expressions
        geom_expr = None
        if lat is not None and lon is not None:
            try:
                geom_expr = f"ST_SetSRID(ST_MakePoint({float(lon)}, {float(lat)}), 4326)"
            except Exception:
                geom_expr = None

        records.append((
            dataset_id,
            OWNER_USER_ID,
            session_id,
            OWNER_USER_ID,
            ts,
            lat,
            lon,
            road_segment_id,
            json.dumps(props),
        ))

    # Use execute_values for bulk insert
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO app_data.events
                (dataset_id, owner_user_id, session_id, user_id, ts, lat, lon,
                 geom, geom_m, road_segment_id, props)
            VALUES %s
            """,
            [
                (
                    r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                    # geom: PostGIS point if lat/lon present
                    f"SRID=4326;POINT({r[6]} {r[5]})" if r[5] is not None and r[6] is not None else None,
                    # geom_m: UTM 15N projection for Iowa
                    None,  # will be computed separately if needed
                    r[7],  # road_segment_id
                    r[8],  # props
                )
                for r in records
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s), %s, %s, %s)",
            page_size=1000,
        )
    conn.commit()
    return len(records)


def update_stats(conn, dataset_id: str, row_count: int) -> None:
    """Update the row_count in the dataset stats."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE app_data.datasets
            SET stats = stats || jsonb_build_object('row_count', %s::bigint,
                                                    'imported_at', now()::text)
            WHERE dataset_id = %s;
        """, (row_count, dataset_id))
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Iowa crash CSV into TitanDashboardAI")
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"Path to crash CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--session", default=DEFAULT_SESSION, help="Session ID (default: iowa_crash_session)")
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help=f"Rows per batch (default: {DEFAULT_CHUNK})")
    parser.add_argument("--dataset-id", default=None, help="Override dataset ID (default: auto-generated)")
    args = parser.parse_args()

    csv_path = args.csv
    if not Path(csv_path).exists():
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    dataset_id = args.dataset_id or f"iowa_crash_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    session_id = args.session

    print(f"Iowa Crash Importer")
    print(f"  CSV:        {csv_path}")
    print(f"  Dataset ID: {dataset_id}")
    print(f"  Session:    {session_id}")
    print(f"  Chunk size: {args.chunk:,}")
    print(f"  PostGIS:    {POSTGIS_DSN}")
    print()

    # Connect
    print("Connecting to PostGIS...", end=" ", flush=True)
    conn = psycopg2.connect(POSTGIS_DSN)
    print("OK")

    # Schema
    ensure_schema(conn)

    # Dataset record
    create_dataset(conn, dataset_id, session_id, csv_path)

    # Count total rows for progress
    print("Counting rows...", end=" ", flush=True)
    total_rows = sum(1 for _ in open(csv_path, encoding="utf-8")) - 1  # subtract header
    print(f"{total_rows:,} data rows")

    # Import
    print(f"\nImporting in chunks of {args.chunk:,}...")
    imported = 0
    skipped = 0
    chunk_num = 0

    for chunk_df in pd.read_csv(csv_path, chunksize=args.chunk, dtype=str, low_memory=False):
        chunk_num += 1
        chunk_rows = []

        for _, row in chunk_df.iterrows():
            # Parse lat/lon
            try:
                lat = float(row.get("LATITUDE") or "")
                lon = float(row.get("LONGITUDE") or "")
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    raise ValueError("Out of range")
            except (ValueError, TypeError):
                skipped += 1
                continue

            # Parse timestamp
            ts = _parse_iowa_timestamp(row)

            # road_segment_id from ROUTEID
            routeid = str(row.get("ROUTEID") or "").strip()
            road_segment_id = routeid if routeid and routeid != " " else None

            chunk_rows.append({
                "ts": ts,
                "lat": lat,
                "lon": lon,
                "road_segment_id": road_segment_id,
                "props": _build_props(row),
            })

        n = import_chunk(conn, chunk_rows, dataset_id, session_id)
        imported += n

        pct = (imported + skipped) / total_rows * 100
        print(f"  Chunk {chunk_num:4d} | inserted {imported:>9,} | skipped {skipped:>6,} | {pct:.1f}% done")

    # Update stats
    update_stats(conn, dataset_id, imported)
    conn.close()

    print()
    print("=" * 60)
    print(f"✓ Import complete!")
    print(f"  Inserted : {imported:,} rows")
    print(f"  Skipped  : {skipped:,} rows (bad lat/lon)")
    print(f"  Dataset  : {dataset_id}")
    print(f"  Session  : {session_id}")
    print()
    print("Next steps:")
    print(f"  1. Build RAMS road lines: python3 scripts/build_rams_roads.py")
    print(f"  2. Open http://localhost:8080")
    print(f"  3. In the chat, ask: 'show all crashes'")
    print("=" * 60)

    try:
        from scripts.build_rams_roads import main as build_rams_roads_main

        print("Building public.roads from RAMS route IDs...")
        build_rams_roads_main()
    except Exception as exc:
        print(f"  (RAMS roads build skipped: {exc})")


if __name__ == "__main__":
    main()

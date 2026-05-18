#!/usr/bin/env python3
"""Apply Iowa demo queryable-field defaults to app_data.datasets (run after import)."""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import psycopg2
from psycopg2.extras import RealDictCursor

from dynamic_analyst.demo_queryable_defaults import merge_iowa_queryable_fields

POSTGIS_DSN = os.environ.get("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=localhost port=5432")
OWNER = os.environ.get("DEFAULT_OWNER_USER_ID", "dev-user")


def main() -> None:
    with psycopg2.connect(POSTGIS_DSN) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT dataset_id, name, entity_type, stats
            FROM app_data.datasets
            WHERE owner_user_id = %s AND status = 'ready'
            """,
            (OWNER,),
        )
        rows = cur.fetchall() or []
        updated = 0
        for row in rows:
            entity = str(row.get("entity_type") or "").lower()
            if entity not in {"crash", "event", "cv"}:
                continue
            stats = row.get("stats") or {}
            if isinstance(stats, str):
                stats = json.loads(stats)
            if not isinstance(stats, dict):
                stats = {}
            merged = merge_iowa_queryable_fields(entity, stats.get("queryable_fields"), force=True)
            stats["queryable_fields"] = merged
            cur.execute(
                """
                UPDATE app_data.datasets
                SET stats = %s::jsonb
                WHERE dataset_id = %s AND owner_user_id = %s
                """,
                (json.dumps(stats), row["dataset_id"], OWNER),
            )
            updated += 1
            print(f"✓ {row['dataset_id']} ({entity}) — {len(merged.get('fields') or [])} queryable fields")
        conn.commit()
    print(f"\nUpdated {updated} dataset(s). Restart chat or clear session for fresh schema cache.")


if __name__ == "__main__":
    main()

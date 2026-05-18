#!/usr/bin/env python3
"""Apply PostGIS bootstrap SQL on a fresh Render (or other cloud) database."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
INIT_FILES = [
    ROOT / "db/init/001_init.sql",
    ROOT / "db/init/002_public_registry.sql",
    ROOT / "db/init/003_auth.sql",
    ROOT / "db/init/004_cv_segment_stats.sql",
]


def main() -> None:
    dsn = os.environ.get("POSTGIS_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("POSTGIS_DSN or DATABASE_URL is required")

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for path in INIT_FILES:
                if not path.exists():
                    print(f"skip missing {path.name}")
                    continue
                sql = path.read_text(encoding="utf-8")
                print(f"applying {path.name}...")
                cur.execute(sql)
        print("database bootstrap complete")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Restore demo-traffic.dump into Render (or any remote) Postgres.
set -euo pipefail

DUMP="${1:-./demo-traffic.dump}"
DSN="${POSTGIS_DSN:-${DATABASE_URL:-}}"

if [[ -z "$DSN" ]]; then
  echo "Set POSTGIS_DSN to the Internal Database URL from Render Dashboard."
  exit 1
fi

if [[ ! -f "$DUMP" ]]; then
  echo "Dump not found: $DUMP (run ./scripts/export-demo-db.sh first)"
  exit 1
fi

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore not found. Install PostgreSQL client tools."
  exit 1
fi

echo "Restoring ${DUMP} to remote database..."
pg_restore --clean --if-exists --no-owner --no-acl --dbname "$DSN" "$DUMP"
echo "Done."

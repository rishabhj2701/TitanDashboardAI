#!/usr/bin/env bash
# Export local PostGIS data for restoring on Render / Neon / any cloud Postgres.
set -euo pipefail

OUT="${1:-./demo-traffic.dump}"
CONTAINER="${POSTGIS_CONTAINER:-postgis}"

echo "Exporting database 'traffic' from container '${CONTAINER}' to ${OUT}"
docker exec "$CONTAINER" pg_dump -U postgres -d traffic -Fc > "$OUT"
ls -lh "$OUT"
echo ""
echo "Restore on Render (from a machine with network access to Render Postgres):"
echo "  pg_restore --clean --no-owner --dbname \"\$POSTGIS_DSN\" ${OUT}"

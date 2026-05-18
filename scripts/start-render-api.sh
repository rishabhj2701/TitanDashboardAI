#!/bin/sh
set -eu

PORT="${PORT:-10000}"
APP_WORKERS="${APP_API_WORKERS:-1}"
TILE_WORKERS="${TILE_API_WORKERS:-1}"

if [ "${RUN_DB_INIT:-0}" = "1" ]; then
  echo ">>> Running database bootstrap (RUN_DB_INIT=1)..."
  python scripts/render_db_init.py || echo "WARN: DB init failed (may already exist)"
fi

sed "s/__PORT__/${PORT}/g" /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo ">>> Starting app API on :8000 (${APP_WORKERS} worker(s))..."
uvicorn server:app --host 127.0.0.1 --port 8000 --workers "$APP_WORKERS" &

echo ">>> Starting tile API on :8001 (${TILE_WORKERS} worker(s))..."
uvicorn tile_server:app --host 127.0.0.1 --port 8001 --workers "$TILE_WORKERS" &

echo ">>> Starting nginx on :${PORT}..."
exec nginx -g 'daemon off;'

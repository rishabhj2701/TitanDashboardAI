#!/usr/bin/env bash
# Fully functional public demo: local Docker (PostGIS + Redis + API + UI) + Cloudflare tunnel.
# Keep this Mac awake and this terminal open while others use the demo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --profile localdb)
PORT="${DEMO_PORT:-8080}"
ORIGIN="http://127.0.0.1:${PORT}"
SEED="${SEED_DEMO:-0}"
WAIT_SECS="${DEMO_WAIT_SECS:-180}"

need_env() {
  local key="$1"
  if [[ ! -f .env ]]; then
    echo "Missing .env — copy .env.example and set GOOGLE_API_KEY and VITE_MAPBOX_TOKEN."
    exit 1
  fi
  # shellcheck disable=SC1091
  set -a && source .env && set +a
  local val="${!key:-}"
  if [[ -z "$val" ]]; then
    echo "Set ${key} in .env (required for a working demo)."
    exit 1
  fi
}

cleanup() {
  echo ""
  echo "Stopping tunnel (Docker keeps running). Use: ./scripts/local-demo.sh down"
  exit 0
}
trap cleanup SIGINT SIGTERM

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared:  brew install cloudflared"
  exit 1
fi

need_env GOOGLE_API_KEY
need_env VITE_MAPBOX_TOKEN

echo "=============================================="
echo " Full demo — Mac Docker + public HTTPS link"
echo "=============================================="
echo ""
echo "Why this path (not Render free):"
echo "  • Full Iowa PostGIS data (no 1 GB cap)"
echo "  • No API sleep / cold starts"
echo "  • Redis included (needed with multiple API workers)"
echo ""

echo ">>> Starting stack (PostGIS, Redis, API, tiles, web)..."
"${COMPOSE[@]}" up -d --build

echo ">>> Waiting for ${ORIGIN}/healthz (up to ${WAIT_SECS}s)..."
deadline=$((SECONDS + WAIT_SECS))
until curl -fsS "${ORIGIN}/healthz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "Timed out. Check: ${COMPOSE[*]} logs"
    exit 1
  fi
  sleep 3
done

if ! curl -fsS "${ORIGIN}/api/capabilities" >/dev/null 2>&1; then
  echo "WARN: /api/capabilities not ready — check app_api logs"
else
  echo ">>> API OK"
fi

if [[ "$SEED" == "1" ]]; then
  echo ">>> Seeding Iowa demo policy..."
  "${COMPOSE[@]}" run --rm -e PYTHONPATH=/app app_api python3 scripts/seed_iowa_demo_policy.py || true
fi

echo ""
echo "Local:  ${ORIGIN}"
echo "Share the https URL below. Press Ctrl+C to stop the tunnel only."
echo ""

exec cloudflared tunnel --url "${ORIGIN}"

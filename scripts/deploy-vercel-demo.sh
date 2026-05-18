#!/usr/bin/env bash
# Deploy Vite frontend to Vercel; API/tiles must be reachable at DEMO_API_URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${VITE_MAPBOX_TOKEN:?Set VITE_MAPBOX_TOKEN in .env}"

DEMO_API_URL="${DEMO_API_URL:-${VITE_API_BASE_URL:-}}"
if [[ -z "$DEMO_API_URL" ]]; then
  echo "DEMO_API_URL is required (public backend base URL, no trailing slash)."
  echo ""
  echo "Option A — tunnel local Docker (fastest for a short demo):"
  echo "  ./scripts/local-demo.sh up"
  echo "  ./scripts/share-demo-tunnel.sh    # copy the https://….trycloudflare.com URL"
  echo "  DEMO_API_URL=https://….trycloudflare.com $0"
  echo ""
  echo "Option B — Render all-in-one backend:"
  echo "  Deploy render.yaml, then:"
  echo "  DEMO_API_URL=https://YOUR-SERVICE.onrender.com $0"
  exit 1
fi

DEMO_API_URL="${DEMO_API_URL%/}"
export VITE_API_BASE_URL="$DEMO_API_URL"
export VITE_TILE_API_BASE_URL="${VITE_TILE_API_BASE_URL:-$DEMO_API_URL}"
export VITE_USE_ROAD_TILES="${VITE_USE_ROAD_TILES:-1}"
export VITE_ROAD_TILES_DATASET="${VITE_ROAD_TILES_DATASET:-iowa_cv_winter_2025}"

if ! curl -fsS "${DEMO_API_URL}/api/capabilities" >/dev/null 2>&1; then
  echo "WARN: ${DEMO_API_URL}/api/capabilities did not respond."
  echo "      Fix backend/tunnel before sharing the Vercel link."
fi

if ! command -v vercel >/dev/null 2>&1; then
  echo "Using npx vercel (install globally: npm i -g vercel)"
fi

echo "Deploying frontend to Vercel…"
echo "  API:  ${VITE_API_BASE_URL}"
echo "  Tiles: ${VITE_TILE_API_BASE_URL}"
echo ""

if [[ -z "${VERCEL_TOKEN:-}" ]] && [[ ! -f .vercel/project.json ]]; then
  echo "First run: you will be prompted to log in and link this project."
fi

npx vercel deploy --prod \
  --build-env VITE_MAPBOX_TOKEN="$VITE_MAPBOX_TOKEN" \
  --build-env VITE_API_BASE_URL="$VITE_API_BASE_URL" \
  --build-env VITE_TILE_API_BASE_URL="$VITE_TILE_API_BASE_URL" \
  --build-env VITE_USE_ROAD_TILES="$VITE_USE_ROAD_TILES" \
  --build-env VITE_ROAD_TILES_DATASET="$VITE_ROAD_TILES_DATASET" \
  --build-env VITE_ENABLE_WEBSITE_BUILDER_ROUTES="${VITE_ENABLE_WEBSITE_BUILDER_ROUTES:-0}" \
  --build-env VITE_ENABLE_CHART_EDITING="${VITE_ENABLE_CHART_EDITING:-0}"

echo ""
echo "Done. Open the Production URL from the output above."
echo "Backend must stay up at: ${DEMO_API_URL}"

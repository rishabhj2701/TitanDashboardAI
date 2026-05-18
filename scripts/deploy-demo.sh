#!/usr/bin/env bash
# Interactive helper for demo deployment (Render recommended, Vercel optional frontend).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " Titan Dashboard AI — Demo Deploy"
echo "=============================================="
echo ""
echo "This app needs PostGIS + Redis + FastAPI. Vercel alone cannot host the full stack."
echo ""
echo "Recommended: Render (one URL, includes UI + APIs)"
echo "  1. Push this repo to GitHub"
echo "  2. https://dashboard.render.com/ → New → Blueprint"
echo "  3. Select render.yaml in this repo"
echo "  4. Set secrets: GOOGLE_API_KEY, VITE_MAPBOX_TOKEN"
echo "  5. After first deploy, set RUN_DB_INIT=0"
echo "  6. Restore DB: ./scripts/export-demo-db.sh && pg_restore to Render Postgres"
echo ""
echo "Optional: Vercel frontend only (after Render backend is live)"
echo "  export VITE_API_BASE_URL=https://YOUR-SERVICE.onrender.com"
echo "  export VITE_TILE_API_BASE_URL=https://YOUR-SERVICE.onrender.com"
echo "  npx vercel deploy --prod"
echo ""
echo "Local smoke test of demo image:"
echo "  docker build -f Dockerfile.demo -t titan-demo:local ."
echo "  docker run --rm -p 8080:80 --env-file .env titan-demo:local"
echo ""

if [[ "${1:-}" == "vercel" ]]; then
  exec npx vercel deploy --prod
fi

if [[ "${1:-}" == "build" ]]; then
  docker build -f Dockerfile.demo -t titan-demo:local .
  echo "Built titan-demo:local"
  exit 0
fi

if [[ "${1:-}" == "export-db" ]]; then
  exec ./scripts/export-demo-db.sh "${2:-./demo-traffic.dump}"
fi

echo "Usage: $0 [vercel|build|export-db]"

#!/usr/bin/env bash
# Step-by-step helper for Render demo deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " Render demo deploy (Static + API + DB + KV)"
echo "=============================================="
echo ""
echo "1. Push this repo to GitHub (if not already)."
echo ""
echo "2. Open https://dashboard.render.com/ → New → Blueprint"
echo "   Connect the repo. Use render.yaml (paid-capable) or render.free.yaml (trial)."
echo ""
echo "3. When prompted, set secrets:"
echo "     GOOGLE_API_KEY"
echo "     VITE_MAPBOX_TOKEN"
echo "     (optional) AGENT_MODEL_DEFAULT, OPENAI_API_KEY"
echo ""
echo "4. Wait for first deploy. titan-api runs RUN_DB_INIT=1 (schema only)."
echo "   Then in Dashboard → titan-api → Environment:"
echo "     RUN_DB_INIT=0"
echo "   Manual Deploy → Clear build cache & deploy."
echo ""
echo "5. Export local DB and restore to Render Postgres:"
echo "     ./scripts/export-demo-db.sh"
echo "     export POSTGIS_DSN='<Internal Database URL from Render Dashboard>'"
echo "     ./scripts/restore-render-db.sh ./demo-traffic.dump"
echo ""
echo "6. Share the static site URL:"
echo "     https://titan-web.onrender.com"
echo ""
echo "Smoke test API:"
echo "  curl -fsS https://titan-api.onrender.com/api/capabilities | head"
echo ""

case "${1:-}" in
  export-db)
    exec ./scripts/export-demo-db.sh "${2:-./demo-traffic.dump}"
    ;;
  restore-db)
    exec ./scripts/restore-render-db.sh "${2:-./demo-traffic.dump}"
    ;;
  *)
    echo "Usage: $0 [export-db [path]|restore-db [dump]]"
    ;;
esac

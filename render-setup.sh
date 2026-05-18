#!/usr/bin/env bash
# Render FREE demo deploy (render.yaml — $0 plans only).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " Render FREE demo (Static + API + Postgres)"
echo "=============================================="
echo ""
echo "Cost: \$0 on Render Hobby (see render.com/docs/free for limits)."
echo ""
echo "Limits you should know:"
echo "  • API (titan-api) sleeps after ~15 min idle — first click may take ~1 min"
echo "  • Postgres is 1 GB max — full Iowa dump may not fit; use tunnel (./scripts/free-demo.sh tunnel) if restore fails"
echo "  • Free Postgres is deleted after 30 days unless you upgrade"
echo "  • No Redis service — sessions use in-memory (fine for demos)"
echo ""
echo "Steps:"
echo "  1. Push repo to GitHub"
echo "  2. https://dashboard.render.com/ → New → Blueprint → this repo"
echo "  3. Set secrets: GOOGLE_API_KEY, VITE_MAPBOX_TOKEN"
echo "  4. After deploy: titan-api → RUN_DB_INIT=0 → redeploy"
echo "  5. Restore DB:"
echo "       ./scripts/export-demo-db.sh"
echo "       export POSTGIS_DSN='<Internal Database URL from Render>'"
echo "       ./scripts/restore-render-db.sh"
echo "  6. Share: https://titan-web.onrender.com"
echo ""
echo "Paid upgrade path: use render.paid.yaml instead of render.yaml"
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

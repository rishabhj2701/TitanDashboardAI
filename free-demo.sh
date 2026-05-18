#!/usr/bin/env bash
# Free demo options — use share-full-demo for a fully working public link.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " Free demo hosting"
echo "=============================================="
echo ""
echo "RECOMMENDED — fully functional (full data, chat, map, crash analysis):"
echo "  ./scripts/share-full-demo.sh"
echo "  (Docker + Redis + PostGIS on this Mac, public link via Cloudflare)"
echo ""
echo "  Optional: keep Mac awake during the demo:"
echo "  caffeinate -dims ./scripts/share-full-demo.sh"
echo ""
echo "Render free tier — NOT fully functional for this app:"
echo "  • API sleeps when idle (~1 min cold start)"
echo "  • Postgres 1 GB (Iowa dump may not fit) + 30-day expiry"
echo "  • No Redis (single API worker only; chat state less reliable)"
echo "  ./scripts/render-setup.sh"
echo ""

case "${1:-}" in
  share|tunnel|full)
    exec ./scripts/share-full-demo.sh
    ;;
  render)
    exec ./scripts/render-setup.sh
    ;;
  local)
    exec ./scripts/local-demo.sh up
    ;;
  *)
    echo "Usage: $0 [share|render|local]"
    ;;
esac

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
echo "Recommended (free, full Iowa data): Oracle Cloud VM + Cloudflare Tunnel"
echo "  1. Create Oracle Always Free ARM VM (Ubuntu, 4 OCPU / 24 GB)"
echo "  2. SSH in and run:  sudo bash scripts/oracle-vm-bootstrap.sh"
echo "  3. On Mac: ./scripts/export-demo-db.sh && ./scripts/oracle-upload-db.sh VM_IP"
echo "  4. On VM: sudo systemctl enable --now titan-demo cloudflared-titan"
echo "  5. Public URL: sudo journalctl -u cloudflared-titan -n 50 | grep trycloudflare"
echo ""
echo "Free demo (pick one):"
echo "  ./scripts/free-demo.sh tunnel   # full data, Mac must stay on"
echo "  ./scripts/free-demo.sh render   # Render \$0 blueprint (render.yaml)"
echo ""
echo "Vercel frontend + public API (shareable *.vercel.app link):"
echo "  ./scripts/local-demo.sh up"
echo "  ./scripts/share-demo-tunnel.sh          # or use Render URL as DEMO_API_URL"
echo "  DEMO_API_URL=https://….trycloudflare.com ./scripts/deploy-vercel-demo.sh"
echo ""
echo "One link only (no Vercel): use the trycloudflare URL from share-demo-tunnel.sh"
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

if [[ "${1:-}" == "oracle-upload" ]]; then
  exec ./scripts/oracle-upload-db.sh "${2:?VM IP}" "${3:-./demo-traffic.dump}"
fi

echo "Usage: $0 [vercel|build|export-db|oracle-upload VM_IP [dump]]"

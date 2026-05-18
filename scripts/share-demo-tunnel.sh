#!/usr/bin/env bash
# Expose local Docker demo (port 8080) as a public HTTPS link via Cloudflare Quick Tunnel.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${DEMO_PORT:-8080}"
ORIGIN="http://127.0.0.1:${PORT}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install:"
  echo "  brew install cloudflared"
  echo "  # or: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  exit 1
fi

if ! curl -fsS "${ORIGIN}/healthz" >/dev/null 2>&1; then
  echo "Nothing listening at ${ORIGIN}. Start the stack first:"
  echo "  ./scripts/local-demo.sh up"
  exit 1
fi

echo "=============================================="
echo " Public demo URL (keep this terminal open )"
echo "=============================================="
echo "Local:  ${ORIGIN}"
echo ""
echo "Share the https://*.trycloudflare.com URL printed below."
echo "For Vercel frontend, use that URL as DEMO_API_URL when deploying."
echo ""

exec cloudflared tunnel --url "${ORIGIN}"

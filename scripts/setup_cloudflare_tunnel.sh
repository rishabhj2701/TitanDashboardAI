#!/usr/bin/env bash
# One-time Cloudflare named tunnel setup for Titan.
# Writes deploy/cloudflare/config.yml and adds TITAN_SHARE_URL to .env.
# Both output files are gitignored.
#
# Usage: ./titan share setup   (or bash scripts/setup_cloudflare_tunnel.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

_red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
_green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
_yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }

require_cloudflared() {
  if ! command -v cloudflared >/dev/null 2>&1; then
    _red "cloudflared not found. Install it first:"
    echo "  brew install cloudflared"
    exit 1
  fi
}

# ── Step 1: login ──────────────────────────────────────────────────────────────
require_cloudflared

echo ""
_green "Step 1/4 — Cloudflare login"
echo "A browser window will open. Log in and authorize cloudflared."
echo "If it does not open automatically, copy the URL printed below."
echo ""
cloudflared tunnel login

# ── Step 2: tunnel name ────────────────────────────────────────────────────────
echo ""
_green "Step 2/4 — Tunnel name"
read -r -p "Enter a short tunnel name (e.g. titan-reactor): " TUNNEL_NAME
TUNNEL_NAME="$(echo "$TUNNEL_NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+|-+$//g' | cut -c1-63)"
if [[ -z "$TUNNEL_NAME" ]]; then
  _red "Tunnel name cannot be empty."
  exit 1
fi

echo "Creating tunnel: $TUNNEL_NAME"
cloudflared tunnel create "$TUNNEL_NAME"

# ── Step 3: hostname ───────────────────────────────────────────────────────────
echo ""
_green "Step 3/4 — Public hostname"
echo "Enter the full hostname where Titan should be reachable."
echo "The zone (e.g. yourdomain.com) must already be on Cloudflare DNS."
read -r -p "Hostname (e.g. titan.yourdomain.com): " HOSTNAME
if [[ -z "$HOSTNAME" ]]; then
  _red "Hostname cannot be empty."
  exit 1
fi

echo "Routing DNS: $HOSTNAME → tunnel $TUNNEL_NAME"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME"

# ── Step 4: write config ───────────────────────────────────────────────────────
echo ""
_green "Step 4/4 — Writing config"

TUNNEL_ID="$(cloudflared tunnel info "$TUNNEL_NAME" 2>/dev/null | grep -oE '[0-9a-f-]{36}' | head -1 || true)"
if [[ -z "$TUNNEL_ID" ]]; then
  _yellow "Could not auto-detect tunnel ID. You may need to fill it in manually."
  TUNNEL_ID="<paste-tunnel-id-here>"
fi

CREDENTIALS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"

mkdir -p "$ROOT/deploy/cloudflare"
CONFIG_FILE="$ROOT/deploy/cloudflare/config.yml"

cat >"$CONFIG_FILE" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CREDENTIALS_FILE}

ingress:
  - hostname: ${HOSTNAME}
    service: http://localhost:8080
  - service: http_status:404
EOF

_green "Wrote: $CONFIG_FILE"

# ── Update .env ────────────────────────────────────────────────────────────────
ENV_FILE="$ROOT/.env"
SHARE_URL="https://${HOSTNAME}"

if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^TITAN_SHARE_URL=" "$ENV_FILE"; then
    sed -i.bak "s|^TITAN_SHARE_URL=.*|TITAN_SHARE_URL=${SHARE_URL}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    _green "Updated TITAN_SHARE_URL in .env"
  else
    echo "" >>"$ENV_FILE"
    echo "TITAN_SHARE_URL=${SHARE_URL}" >>"$ENV_FILE"
    _green "Added TITAN_SHARE_URL to .env"
  fi
else
  echo "TITAN_SHARE_URL=${SHARE_URL}" >"$ENV_FILE"
  _green "Created .env with TITAN_SHARE_URL"
fi

# ── Ensure gitignore covers output files ──────────────────────────────────────
GITIGNORE="$ROOT/.gitignore"
if [[ -f "$GITIGNORE" ]]; then
  grep -q "deploy/cloudflare/" "$GITIGNORE" || echo "deploy/cloudflare/" >>"$GITIGNORE"
fi

echo ""
_green "Setup complete!"
echo ""
echo "  Tunnel name : $TUNNEL_NAME"
echo "  Tunnel ID   : $TUNNEL_ID"
echo "  Public URL  : $SHARE_URL"
echo "  Config file : $CONFIG_FILE"
echo ""
echo "Each session:"
echo "  ./titan start"
echo "  ./titan share anyone          # opens $SHARE_URL"
echo "  ./titan share anyone --detach # background"
echo ""
_yellow "Keep your laptop on and Titan running while sharing."

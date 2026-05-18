#!/usr/bin/env bash
# Stop tunnel and Docker — no background CPU/battery use from the demo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --profile localdb)
STATE_DIR="$ROOT/.demo"
TUNNEL_PID="$STATE_DIR/cloudflared.pid"

stop_tunnel() {
  if [[ -f "$TUNNEL_PID" ]]; then
    local pid
    pid="$(cat "$TUNNEL_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      echo ">>> Stopping cloudflared (pid ${pid})..."
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$TUNNEL_PID"
  fi
  pkill -f "cloudflared tunnel --url http://127.0.0.1:${DEMO_PORT:-8080}" 2>/dev/null || true
  pkill -f "cloudflared tunnel --url http://localhost:${DEMO_PORT:-8080}" 2>/dev/null || true
}

echo ">>> Stopping public tunnel..."
stop_tunnel

echo ">>> Stopping Docker stack..."
"${COMPOSE[@]}" down --remove-orphans 2>/dev/null || "${COMPOSE[@]}" down 2>/dev/null || true

rm -f "$STATE_DIR/public-url" "$STATE_DIR/tunnel.log" 2>/dev/null || true

echo ""
echo "Demo is OFF (no tunnel, no containers)."
echo "Start again: ./demo-on.sh"

#!/usr/bin/env bash
# Start Docker demo + Cloudflare tunnel (background). Prints shareable URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --profile localdb)
PORT="${DEMO_PORT:-8080}"
ORIGIN="http://127.0.0.1:${PORT}"
STATE_DIR="$ROOT/.demo"
TUNNEL_LOG="$STATE_DIR/tunnel.log"
TUNNEL_PID="$STATE_DIR/cloudflared.pid"
PUBLIC_URL_FILE="$STATE_DIR/public-url"
WAIT_SECS="${DEMO_WAIT_SECS:-180}"

mkdir -p "$STATE_DIR"

if [[ -f "$TUNNEL_PID" ]] && kill -0 "$(cat "$TUNNEL_PID")" 2>/dev/null; then
  echo "Demo already running."
  [[ -f "$PUBLIC_URL_FILE" ]] && echo "Public: $(cat "$PUBLIC_URL_FILE")"
  echo "Local:  ${ORIGIN}"
  echo "Stop with: ./demo-off.sh"
  exit 0
fi

if [[ ! -f .env ]]; then
  created=0
  for template in .env.example .env.demo.example; do
    if [[ -f "$template" ]]; then
      cp "$template" .env
      created=1
      break
    fi
  done
  if [[ "$created" -eq 0 ]]; then
    echo "Missing .env — create one (see .env.example or .env.demo.example)."
    exit 1
  fi
  echo "Created .env from template — add VITE_MAPBOX_TOKEN and an agent key, then run again."
  exit 1
fi

set -a && source .env && set +a
if [[ -z "${GOOGLE_API_KEY:-}" && -z "${AGENT_LITELLM_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Set GOOGLE_API_KEY or AGENT_LITELLM_API_KEY or OPENAI_API_KEY in .env"
  exit 1
fi
if [[ -z "${VITE_MAPBOX_TOKEN:-}" ]]; then
  echo "Set VITE_MAPBOX_TOKEN in .env"
  exit 1
fi

if [[ "${POSTGIS_DSN:-}" != postgresql://* && "${POSTGIS_DSN:-}" != postgres://* ]]; then
  if [[ "${POSTGIS_DSN:-}" == *" host="* ]] || [[ "${POSTGIS_DSN:-}" == *" host="* ]]; then
    echo "WARN: POSTGIS_DSN uses spaces — Docker may only see dbname=."
    echo "      Use: POSTGIS_DSN=postgresql://postgres:PASSWORD@postgis:5432/traffic"
  fi
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared: brew install cloudflared"
  exit 1
fi

echo ">>> Starting Docker (PostGIS, Redis, API, tiles, web)..."
"${COMPOSE[@]}" up -d --build

echo ">>> Waiting for app (up to ${WAIT_SECS}s)..."
deadline=$((SECONDS + WAIT_SECS))
until curl -fsS "${ORIGIN}/healthz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "Timed out. Check: docker compose --profile localdb logs"
    exit 1
  fi
  sleep 3
done

if [[ "${SEED_DEMO:-0}" == "1" ]]; then
  echo ">>> Seeding Iowa demo policy..."
  "${COMPOSE[@]}" run --rm -e PYTHONPATH=/app app_api python3 scripts/seed_iowa_demo_policy.py || true
fi

rm -f "$TUNNEL_LOG" "$TUNNEL_PID" "$PUBLIC_URL_FILE"
echo ">>> Starting public tunnel..."
nohup cloudflared tunnel --url "${ORIGIN}" >"$TUNNEL_LOG" 2>&1 &
echo $! >"$TUNNEL_PID"

public_url=""
for _ in $(seq 1 45); do
  if public_url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)"; then
    [[ -n "$public_url" ]] && break
  fi
  if ! kill -0 "$(cat "$TUNNEL_PID")" 2>/dev/null; then
    echo "cloudflared exited. Log:"
    tail -30 "$TUNNEL_LOG"
    exit 1
  fi
  sleep 1
done

if [[ -z "$public_url" ]]; then
  echo "Could not read public URL yet. Check: tail -f $TUNNEL_LOG"
  exit 1
fi

echo "$public_url" >"$PUBLIC_URL_FILE"

echo ""
echo "=============================================="
echo " Demo is ON"
echo "=============================================="
echo "  Local:   ${ORIGIN}"
echo "  Share:   ${public_url}"
echo ""
echo "  Stop (tunnel + Docker, saves power):  ./demo-off.sh"
echo "=============================================="

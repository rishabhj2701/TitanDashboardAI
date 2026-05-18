#!/usr/bin/env bash
# Control Oracle demo stack (run on VM or via ssh user@vm 'sudo bash -s' < scripts/oracle-demo.sh up)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose --profile localdb -f docker-compose.yml -f docker-compose.oracle.yml)

cmd="${1:-status}"

case "$cmd" in
  up)
    "${COMPOSE[@]}" up -d --build
    ;;
  down)
    "${COMPOSE[@]}" down
    ;;
  restart)
    "${COMPOSE[@]}" up -d --build --force-recreate
    ;;
  logs)
    "${COMPOSE[@]}" logs -f --tail=200 "${2:-}"
    ;;
  ps)
    "${COMPOSE[@]}" ps
    ;;
  url)
    journalctl -u cloudflared-titan --no-pager -n 80 2>/dev/null | grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' | tail -1 || true
    ;;
  status)
    "${COMPOSE[@]}" ps
    echo ""
    echo "Tunnel URL:"
    "${ROOT}/scripts/oracle-demo.sh" url || echo "(start cloudflared-titan service)"
    ;;
  *)
    echo "Usage: $0 {up|down|restart|logs|ps|url|status} [service]"
    exit 1
    ;;
esac

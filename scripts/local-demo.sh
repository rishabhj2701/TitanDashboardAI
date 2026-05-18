#!/usr/bin/env bash
# Local Docker demo control (no Oracle VM).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose --profile localdb)

cmd="${1:-up}"

case "$cmd" in
  up)
    if [[ ! -f .env ]]; then
      cp .env.example .env
      echo "Created .env from .env.example — add GOOGLE_API_KEY and VITE_MAPBOX_TOKEN, then run again."
      exit 1
    fi
    "${COMPOSE[@]}" up -d --build
    echo ""
    echo "Open: http://localhost:8080"
    echo "API:  http://localhost:8000/api/capabilities"
    "${COMPOSE[@]}" ps
    ;;
  down)
    "${COMPOSE[@]}" down
    ;;
  restart)
    "${COMPOSE[@]}" up -d --build --force-recreate
    ;;
  logs)
    "${COMPOSE[@]}" logs -f "${2:-}"
    ;;
  ps)
    "${COMPOSE[@]}" ps
    ;;
  seed)
    "${COMPOSE[@]}" run --rm -e PYTHONPATH=/app app_api python3 scripts/seed_iowa_demo_policy.py
    ;;
  *)
    echo "Usage: $0 {up|down|restart|logs|ps|seed} [service]"
    exit 1
    ;;
esac

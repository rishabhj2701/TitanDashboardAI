#!/usr/bin/env bash
# Stop demo: tunnel + all Docker containers (saves battery/CPU).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/demo-off.sh"

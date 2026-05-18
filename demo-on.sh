#!/usr/bin/env bash
# Start full demo: Docker stack + public share link. Run ./demo-off.sh to stop everything.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/demo-on.sh"

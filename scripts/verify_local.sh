#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ROUTE_BASELINE_FILE="${ROUTE_BASELINE_FILE:-tests/contracts/routes.baseline.txt}"
ROUTE_CURRENT_FILE="${ROUTE_CURRENT_FILE:-/tmp/routes.after.txt}"
ROUTE_RAW_FILE="${ROUTE_CURRENT_FILE}.raw"
PYTEST_TARGETS=(
  tests/test_auth_router.py
  tests/test_capabilities_router.py
  tests/test_roads_bbox_router.py
  tests/test_roads_tiles_router.py
  tests/test_db_pool.py
  tests/test_session_datasets_router.py
  tests/test_server_preflight.py
  tests/test_tile_server_preflight.py
  tests/test_preflight_script.py
)

echo "==> Frontend tests"
npm test

echo "==> TypeScript compile"
npx tsc --noEmit -p tsconfig.app.json

echo "==> Python tests (local first, Docker fallback)"
if python - <<'PY' >/dev/null 2>&1
import importlib.util
required = ("pytest", "psycopg2", "jose")
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(0 if not missing else 1)
PY
then
  if PYTHONPATH=. pytest -q "${PYTEST_TARGETS[@]}"; then
    echo "Local Python tests passed."
  else
    echo "Local Python tests failed; trying Dockerized fallback..."
    docker compose run --rm --no-deps -v "${ROOT_DIR}:/app" -w /app app_api sh -lc \
      "python -m pip install --quiet pytest && PYTHONPATH=/app pytest -q ${PYTEST_TARGETS[*]}"
  fi
else
  echo "Local Python dependencies missing; using Dockerized fallback..."
  docker compose run --rm --no-deps -v "${ROOT_DIR}:/app" -w /app app_api sh -lc \
    "python -m pip install --quiet pytest && PYTHONPATH=/app pytest -q ${PYTEST_TARGETS[*]}"
fi

echo "==> Route contract dump"
if python scripts/dump_routes.py --module server --module tile_server > "${ROUTE_RAW_FILE}" 2>/dev/null; then
  :
else
  echo "Local route dump unavailable; using Dockerized route dump..."
  docker compose run --rm --no-deps -v "${ROOT_DIR}:/app" -w /app app_api sh -lc \
    "PYTHONPATH=/app python scripts/dump_routes.py --module server --module tile_server" \
    > "${ROUTE_RAW_FILE}"
fi

awk '/^\[/{print} /^[A-Z]+ \//{print}' "${ROUTE_RAW_FILE}" > "${ROUTE_CURRENT_FILE}"
rm -f "${ROUTE_RAW_FILE}"

if [[ -s "${ROUTE_BASELINE_FILE}" ]]; then
  echo "==> Route parity diff"
  diff -u "${ROUTE_BASELINE_FILE}" "${ROUTE_CURRENT_FILE}"
  echo "Route parity: OK"
else
  echo "Route baseline file missing or empty: ${ROUTE_BASELINE_FILE}"
  echo "Saved current routes to: ${ROUTE_CURRENT_FILE}"
fi

echo "verify_local.sh completed successfully."

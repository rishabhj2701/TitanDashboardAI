#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"

curl_json() {
  local url="$1"
  local tmp_body
  local status
  tmp_body="$(mktemp)"
  status="$(curl -sS -o "${tmp_body}" -w "%{http_code}" "${url}")"
  printf "%s %s\n" "${status}" "${tmp_body}"
}

echo "==> Smoke check: /healthz"
health_status="$(curl -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/healthz")"
[[ "${health_status}" == "200" ]] || { echo "/healthz expected 200, got ${health_status}" >&2; exit 1; }

echo "==> Smoke check: /api/capabilities"
read -r cap_status cap_body < <(curl_json "${BASE_URL}/api/capabilities")
[[ "${cap_status}" == "200" ]] || { echo "/api/capabilities expected 200, got ${cap_status}" >&2; cat "${cap_body}" >&2; exit 1; }
python - "${cap_body}" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    payload = json.load(f)
assert "version" in payload, "missing capabilities.version"
assert "features" in payload, "missing capabilities.features"
PY
rm -f "${cap_body}"

echo "==> Smoke check: /api/roads/bbox"
read -r bbox_status bbox_body < <(curl_json "${BASE_URL}/api/roads/bbox")
[[ "${bbox_status}" == "200" || "${bbox_status}" == "304" ]] || {
  echo "/api/roads/bbox expected 200/304, got ${bbox_status}" >&2
  cat "${bbox_body}" >&2
  exit 1
}
if [[ "${bbox_status}" == "200" ]]; then
  python - "${bbox_body}" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    payload = json.load(f)
assert "bbox" in payload, "missing bbox key"
PY
fi
rm -f "${bbox_body}"

echo "==> Smoke check: /tiles/roads/7/19/48.mvt"
tile_headers="$(mktemp)"
tile_body="$(mktemp)"
tile_status="$(curl -sS -D "${tile_headers}" -o "${tile_body}" -w "%{http_code}" "${BASE_URL}/tiles/roads/7/19/48.mvt")"
case "${tile_status}" in
  200|204|304|404) ;;
  *)
    echo "/tiles/roads expected one of 200/204/304/404, got ${tile_status}" >&2
    cat "${tile_body}" >&2
    rm -f "${tile_headers}" "${tile_body}"
    exit 1
    ;;
esac
if [[ "${tile_status}" == "200" ]]; then
  if ! grep -qi '^Content-Type: application/vnd.mapbox-vector-tile' "${tile_headers}"; then
    echo "tile content-type mismatch for 200 response" >&2
    cat "${tile_headers}" >&2
    rm -f "${tile_headers}" "${tile_body}"
    exit 1
  fi
fi
rm -f "${tile_headers}" "${tile_body}"

echo "smoke_compose.sh completed successfully."

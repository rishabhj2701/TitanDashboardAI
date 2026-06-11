#!/usr/bin/env bash
set -euo pipefail

app_env="$(echo "${APP_ENV:-development}" | tr '[:upper:]' '[:lower:]' | xargs)"
dev_jwt_secret="dev-insecure-jwt-secret-change-me"

is_truthy() {
  case "$(echo "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs)" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

require_non_empty() {
  local var_name="$1"
  local value="${!var_name:-}"
  if [[ -z "$(echo "${value}" | xargs)" ]]; then
    errors+=("${var_name} must be set.")
  fi
}

errors=()
warnings=()

if [[ "${app_env}" != "production" && "${app_env}" != "prod" ]]; then
  echo "Preflight skipped: APP_ENV=${APP_ENV:-development}"
  exit 0
fi

require_non_empty "JWT_SECRET"
require_non_empty "CORS_ALLOW_ORIGINS"
require_non_empty "POSTGIS_DSN"

if [[ "${JWT_SECRET:-}" == "${dev_jwt_secret}" ]]; then
  errors+=("JWT_SECRET cannot use the insecure development default.")
fi

if [[ "${CORS_ALLOW_ORIGINS:-}" == *"*"* ]]; then
  errors+=("CORS_ALLOW_ORIGINS cannot include '*'.")
fi

if ! is_truthy "${REQUIRE_USER_ID:-1}"; then
  errors+=("REQUIRE_USER_ID must be enabled in production.")
fi

if is_truthy "${ALLOW_DEBUG_USER_HEADER:-0}"; then
  errors+=("ALLOW_DEBUG_USER_HEADER must be disabled in production.")
fi

if is_truthy "${REQUEST_TIMING_INCLUDE_QUERY:-0}"; then
  errors+=("REQUEST_TIMING_INCLUDE_QUERY must be disabled in production.")
fi

if [[ -n "${TILE_CORS_ALLOW_ORIGINS:-}" && "${TILE_CORS_ALLOW_ORIGINS:-}" == *"*"* ]]; then
  errors+=("TILE_CORS_ALLOW_ORIGINS cannot include '*' when explicitly set in production.")
fi

if [[ -z "${TILE_CORS_ALLOW_ORIGINS:-}" ]]; then
  warnings+=("TILE_CORS_ALLOW_ORIGINS is unset; tile service CORS remains code-default and should be restricted at the edge.")
fi

if [[ ${#errors[@]} -gt 0 ]]; then
  printf 'Preflight failed with %d issue(s):\n' "${#errors[@]}" >&2
  for issue in "${errors[@]}"; do
    printf ' - %s\n' "${issue}" >&2
  done
  exit 1
fi

if [[ ${#warnings[@]} -gt 0 ]]; then
  printf 'Preflight warning(s):\n'
  for warning in "${warnings[@]}"; do
    printf ' - %s\n' "${warning}"
  done
fi

echo "Preflight passed for production."

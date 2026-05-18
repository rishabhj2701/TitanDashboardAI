#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/eval_agent.sh --tier smoke|deep [options]

Options:
  --tier <smoke|deep>         Eval tier to run (default: smoke)
  --base-url <url>            Override EVAL_BASE_URL
  --auth-token <token>        Bearer token for protected /api/chat (preferred)
  --auth-cookie <cookie>      Raw Cookie header value (fallback mode)
  --auth-email <email>        Login email for auto token retrieval via /api/auth/login
  --auth-password <password>  Login password for auto token retrieval via /api/auth/login
  --auth-login-path <path>    Login endpoint path (default: /api/auth/login)
  --queries-file <abs-path>   Optional TSV: complexity<TAB>domain<TAB>prompt
  --scenario-file <path>      Override scenario JSON file
  --baseline-summary <path>   Optional baseline summary.json to compare and gate
  --max-pass-rate-drop <n>    Gate threshold (default: 0.0)
  --max-newly-failing <n>     Gate threshold (default: 0)
  --max-added-failed <n>      Gate threshold (default: 0)
  --max-p95-regression-ms <n> Gate threshold (default: 0)
  --allow-removed-scenarios   Allow new run to omit baseline scenarios
  --out-dir <path>            Output directory (default: artifacts/evals/<timestamp>_<tier>)
  --strict                    Enable strict expected-pattern checks
  --include-user-queries      Include tests/scenarios/user_queries.json
  -h, --help                  Show this help
EOF
}

tier="smoke"
base_url="${EVAL_BASE_URL:-}"
queries_file=""
scenario_file=""
out_dir=""
strict="0"
include_user_queries="0"
baseline_summary=""
max_pass_rate_drop="0.0"
max_newly_failing="0"
max_added_failed="0"
max_p95_regression_ms="0"
allow_removed_scenarios="0"
auth_token="${EVAL_AUTH_TOKEN:-}"
auth_cookie="${EVAL_AUTH_COOKIE:-}"
auth_email="${EVAL_AUTH_EMAIL:-${TEST_USER_EMAIL:-}}"
auth_password="${EVAL_AUTH_PASSWORD:-${TEST_USER_PASSWORD:-}}"
auth_login_path="${EVAL_AUTH_LOGIN_PATH:-/api/auth/login}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)
      tier="${2:-}"
      shift 2
      ;;
    --base-url)
      base_url="${2:-}"
      shift 2
      ;;
    --queries-file)
      queries_file="${2:-}"
      shift 2
      ;;
    --scenario-file)
      scenario_file="${2:-}"
      shift 2
      ;;
    --baseline-summary)
      baseline_summary="${2:-}"
      shift 2
      ;;
    --max-pass-rate-drop)
      max_pass_rate_drop="${2:-}"
      shift 2
      ;;
    --max-newly-failing)
      max_newly_failing="${2:-}"
      shift 2
      ;;
    --max-added-failed)
      max_added_failed="${2:-}"
      shift 2
      ;;
    --max-p95-regression-ms)
      max_p95_regression_ms="${2:-}"
      shift 2
      ;;
    --allow-removed-scenarios)
      allow_removed_scenarios="1"
      shift
      ;;
    --auth-token)
      auth_token="${2:-}"
      shift 2
      ;;
    --auth-cookie)
      auth_cookie="${2:-}"
      shift 2
      ;;
    --auth-email)
      auth_email="${2:-}"
      shift 2
      ;;
    --auth-password)
      auth_password="${2:-}"
      shift 2
      ;;
    --auth-login-path)
      auth_login_path="${2:-}"
      shift 2
      ;;
    --out-dir)
      out_dir="${2:-}"
      shift 2
      ;;
    --strict)
      strict="1"
      shift
      ;;
    --include-user-queries)
      include_user_queries="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "${tier}" != "smoke" && "${tier}" != "deep" ]]; then
  echo "--tier must be smoke or deep (got: ${tier})" >&2
  exit 2
fi

if [[ -n "${queries_file}" && ! -f "${queries_file}" ]]; then
  echo "Queries file not found: ${queries_file}" >&2
  exit 2
fi
if [[ -n "${scenario_file}" && ! -f "${scenario_file}" ]]; then
  echo "Scenario file not found: ${scenario_file}" >&2
  exit 2
fi
if [[ -n "${baseline_summary}" && ! -f "${baseline_summary}" ]]; then
  echo "Baseline summary not found: ${baseline_summary}" >&2
  exit 2
fi

if [[ -z "${out_dir}" ]]; then
  out_dir="artifacts/evals/$(date +%Y%m%d_%H%M%S)_${tier}"
fi
mkdir -p "${out_dir}"

export RUN_AGENT_EVAL=1
export AGENT_EVAL_TIER="${tier}"
if [[ -n "${scenario_file}" ]]; then
  export AGENT_EVAL_SCENARIO_FILE="${scenario_file}"
else
  export AGENT_EVAL_SCENARIO_FILE="tests/scenarios/agent_${tier}.json"
fi
export AGENT_EVAL_OUTPUT_DIR="${out_dir}"
export AGENT_EVAL_STRICT="${strict}"
export AGENT_EVAL_INCLUDE_USER_QUERIES="${include_user_queries}"
export EVAL_AUTH_TOKEN="${auth_token}"
export EVAL_AUTH_COOKIE="${auth_cookie}"
export EVAL_AUTH_EMAIL="${auth_email}"
export EVAL_AUTH_PASSWORD="${auth_password}"
export EVAL_AUTH_LOGIN_PATH="${auth_login_path}"

if [[ -n "${base_url}" ]]; then
  export EVAL_BASE_URL="${base_url}"
fi

if [[ -n "${queries_file}" ]]; then
  export AGENT_EVAL_QUERIES_FILE="${queries_file}"
fi

echo "Running agent eval"
echo "  tier=${AGENT_EVAL_TIER}"
echo "  scenario_file=${AGENT_EVAL_SCENARIO_FILE}"
echo "  out_dir=${AGENT_EVAL_OUTPUT_DIR}"
echo "  base_url=${EVAL_BASE_URL:-auto-detect}"
echo "  strict=${AGENT_EVAL_STRICT}"
echo "  include_user_queries=${AGENT_EVAL_INCLUDE_USER_QUERIES}"
echo "  auth_token=$([[ -n "${EVAL_AUTH_TOKEN:-}" ]] && echo present || echo absent)"
echo "  auth_cookie=$([[ -n "${EVAL_AUTH_COOKIE:-}" ]] && echo present || echo absent)"
echo "  auth_email=$([[ -n "${EVAL_AUTH_EMAIL:-}" ]] && echo present || echo absent)"
echo "  auth_login_path=${EVAL_AUTH_LOGIN_PATH}"
echo "  baseline_summary=${baseline_summary:-none}"

pytest_report_file="${out_dir}/pytest_report.json"

set +e
if command -v rg >/dev/null 2>&1; then
  has_json_report="$(pytest --help 2>/dev/null | rg -q -- "--json-report"; echo $?)"
else
  has_json_report="$(pytest --help 2>/dev/null | grep -q -- "--json-report"; echo $?)"
fi

if [[ "${has_json_report}" == "0" ]]; then
  pytest -q tests/test_agent_eval.py --json-report --json-report-file "${pytest_report_file}"
  status=$?
else
  pytest -q tests/test_agent_eval.py
  status=$?
  cat >"${pytest_report_file}" <<EOF
{"tool":"pytest","json_report_plugin":false,"exit_code":${status},"note":"pytest-json-report plugin unavailable; minimal report generated by scripts/eval_agent.sh"}
EOF
fi
set -e

if [[ ${status} -ne 0 ]]; then
  echo "Agent eval failed (exit=${status}). Artifacts: ${out_dir}" >&2
  exit ${status}
fi

echo "Agent eval completed. Artifacts:"
echo "  ${out_dir}/raw_results.json"
echo "  ${out_dir}/summary.json"
echo "  ${out_dir}/summary.md"
echo "  ${pytest_report_file}"

if [[ -n "${baseline_summary}" ]]; then
  compare_report="${out_dir}/compare_report.json"
  gate_report="${out_dir}/gate_report.json"
  compare_args=(
    python scripts/eval_compare.py
    --base "${baseline_summary}"
    --new "${out_dir}/summary.json"
    --fail-on-regression
    --max-pass-rate-drop "${max_pass_rate_drop}"
    --max-newly-failing "${max_newly_failing}"
    --max-added-failed "${max_added_failed}"
    --max-p95-regression-ms "${max_p95_regression_ms}"
    --gate-report "${gate_report}"
    --json
  )
  if [[ "${allow_removed_scenarios}" == "1" ]]; then
    compare_args+=(--allow-removed-scenarios)
  fi

  echo "Running baseline comparison/gate..."
  if "${compare_args[@]}" > "${compare_report}"; then
    echo "Regression gate passed."
  else
    status=$?
    echo "Regression gate failed (exit=${status}). See:" >&2
    echo "  ${compare_report}" >&2
    echo "  ${gate_report}" >&2
    exit ${status}
  fi
  echo "  ${compare_report}"
  echo "  ${gate_report}"
fi

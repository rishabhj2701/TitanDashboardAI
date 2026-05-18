"""Support utilities for agent regression evaluations."""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

TRUTHY_VALUES = {"1", "true", "yes", "on"}
ALLOWED_TIERS = {"smoke", "deep"}
ALLOWED_COMPLEXITY = {"simple", "in_depth"}
ALLOWED_ASSERTION_KEYS = {
    "response_not_empty",
    "no_error",
    "has_map",
    "no_timeout_text",
    "has_clarification",
    "contains_number",
    "expected_patterns_required",
    "expected_pattern_mode",
}
DEFAULT_TIMEOUT_SECONDS = 120

ERROR_TEXT_RE = re.compile(r"(?i)\b(error|exception|traceback|failed)\b")
TIMEOUT_TEXT_RE = re.compile(r"(?i)\b(timeout|timed out|statement timeout|querycanceled)\b")
CLARIFICATION_RE = re.compile(
    r"(?i)(which (dataset|domain|data)|for which (dataset|domain)|could you clarify|traffic,\s*crash,\s*or workzone)"
)


class AgentEvalConfigError(ValueError):
    """Raised when scenario/config input is invalid."""


@dataclass
class EvalRuntimeConfig:
    tier: str
    scenario_file: str
    output_dir: str
    base_url: str
    auth_cookie: str
    auth_token: str
    auth_email: str
    auth_password: str
    auth_login_path: str
    strict_mode: bool
    include_user_queries: bool
    queries_file: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def _is_truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in TRUTHY_VALUES


def _probe_reachable(base_url: str, timeout_seconds: float = 1.0) -> bool:
    probe_url = f"{base_url.rstrip('/')}/api/capabilities"
    try:
        resp = httpx.get(probe_url, timeout=timeout_seconds)
        return resp.status_code < 500
    except Exception:
        return False


def resolve_base_url(explicit_base_url: str = "") -> str:
    explicit = (explicit_base_url or os.environ.get("EVAL_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    for candidate in ("http://localhost:8000", "http://localhost:8080"):
        if _probe_reachable(candidate):
            return candidate
    return "http://localhost:8000"


def build_runtime_config() -> EvalRuntimeConfig:
    tier = (os.environ.get("AGENT_EVAL_TIER") or "smoke").strip().lower()
    if tier not in ALLOWED_TIERS:
        raise AgentEvalConfigError(f"AGENT_EVAL_TIER must be one of {sorted(ALLOWED_TIERS)}")

    scenario_file = (os.environ.get("AGENT_EVAL_SCENARIO_FILE") or f"tests/scenarios/agent_{tier}.json").strip()
    output_dir = (os.environ.get("AGENT_EVAL_OUTPUT_DIR") or "").strip()
    if not output_dir:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_dir = f"artifacts/evals/{timestamp}_{tier}"
    return EvalRuntimeConfig(
        tier=tier,
        scenario_file=scenario_file,
        output_dir=output_dir,
        base_url=resolve_base_url(),
        auth_cookie=(os.environ.get("EVAL_AUTH_COOKIE") or "").strip(),
        auth_token=(os.environ.get("EVAL_AUTH_TOKEN") or "").strip(),
        auth_email=(os.environ.get("EVAL_AUTH_EMAIL") or os.environ.get("TEST_USER_EMAIL") or "").strip(),
        auth_password=(os.environ.get("EVAL_AUTH_PASSWORD") or os.environ.get("TEST_USER_PASSWORD") or "").strip(),
        auth_login_path=(os.environ.get("EVAL_AUTH_LOGIN_PATH") or "/api/auth/login").strip() or "/api/auth/login",
        strict_mode=_is_truthy(os.environ.get("AGENT_EVAL_STRICT")),
        include_user_queries=_is_truthy(os.environ.get("AGENT_EVAL_INCLUDE_USER_QUERIES")),
        queries_file=(os.environ.get("AGENT_EVAL_QUERIES_FILE") or "").strip(),
        timeout_seconds=int(os.environ.get("AGENT_EVAL_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS),
    )


def _read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise AgentEvalConfigError(f"Scenario file not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _validate_assertions(assertions: Dict[str, Any], scenario_id: str, context: str) -> Dict[str, Any]:
    if not isinstance(assertions, dict):
        raise AgentEvalConfigError(f"{scenario_id}: {context} assertions must be an object")
    unknown = sorted(set(assertions.keys()) - ALLOWED_ASSERTION_KEYS)
    if unknown:
        raise AgentEvalConfigError(f"{scenario_id}: unsupported assertion keys: {unknown}")
    mode = assertions.get("expected_pattern_mode")
    if mode is not None and str(mode).lower() not in {"any", "all"}:
        raise AgentEvalConfigError(f"{scenario_id}: expected_pattern_mode must be 'any' or 'all'")
    return assertions


def _validate_turn(turn: Dict[str, Any], scenario_id: str, index: int) -> Dict[str, Any]:
    if not isinstance(turn, dict):
        raise AgentEvalConfigError(f"{scenario_id}: turn #{index + 1} must be an object")
    user_prompt = str(turn.get("user") or "").strip()
    if not user_prompt:
        raise AgentEvalConfigError(f"{scenario_id}: turn #{index + 1} is missing non-empty 'user'")

    assertions = _validate_assertions(dict(turn.get("assertions") or {}), scenario_id, f"turn #{index + 1}")

    expected_patterns = turn.get("expected_patterns") or []
    forbidden_patterns = turn.get("forbidden_patterns") or []
    if not isinstance(expected_patterns, list) or any(not str(p).strip() for p in expected_patterns):
        raise AgentEvalConfigError(f"{scenario_id}: turn #{index + 1} expected_patterns must be a list of regex strings")
    if not isinstance(forbidden_patterns, list) or any(not str(p).strip() for p in forbidden_patterns):
        raise AgentEvalConfigError(f"{scenario_id}: turn #{index + 1} forbidden_patterns must be a list of regex strings")

    return {
        "user": user_prompt,
        "assertions": assertions,
        "expected_patterns": [str(p).strip() for p in expected_patterns],
        "forbidden_patterns": [str(p).strip() for p in forbidden_patterns],
    }


def validate_scenario(raw: Dict[str, Any], default_tier: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentEvalConfigError("Scenario entries must be JSON objects")

    scenario_id = str(raw.get("id") or "").strip()
    if not scenario_id:
        raise AgentEvalConfigError("Scenario is missing required 'id'")

    tier = str(raw.get("tier") or default_tier).strip().lower()
    if tier not in ALLOWED_TIERS:
        raise AgentEvalConfigError(f"{scenario_id}: tier must be one of {sorted(ALLOWED_TIERS)}")

    complexity = str(raw.get("complexity") or "simple").strip().lower()
    if complexity not in ALLOWED_COMPLEXITY:
        raise AgentEvalConfigError(f"{scenario_id}: complexity must be one of {sorted(ALLOWED_COMPLEXITY)}")

    domain = str(raw.get("domain") or "").strip().lower()
    if not domain:
        raise AgentEvalConfigError(f"{scenario_id}: domain is required")

    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise AgentEvalConfigError(f"{scenario_id}: turns must be a non-empty list")
    turns = [_validate_turn(turn, scenario_id, idx) for idx, turn in enumerate(raw_turns)]

    final_assertions = _validate_assertions(dict(raw.get("final_assertions") or {}), scenario_id, "final")
    return {
        "id": scenario_id,
        "tier": tier,
        "complexity": complexity,
        "domain": domain,
        "turns": turns,
        "final_assertions": final_assertions,
    }


def _custom_scenario_from_row(
    line_number: int,
    complexity: str,
    domain: str,
    prompt: str,
    tier: str,
    id_prefix: str = "custom",
) -> Dict[str, Any]:
    norm_complexity = complexity.strip().lower()
    if norm_complexity not in ALLOWED_COMPLEXITY:
        raise AgentEvalConfigError(
            f"Custom queries line {line_number}: complexity must be one of {sorted(ALLOWED_COMPLEXITY)}"
        )
    norm_domain = domain.strip().lower()
    if not norm_domain:
        raise AgentEvalConfigError(f"Custom queries line {line_number}: domain is required")
    user_prompt = prompt.strip()
    if not user_prompt:
        raise AgentEvalConfigError(f"Custom queries line {line_number}: prompt is required")

    return {
        "id": f"{id_prefix}_{line_number}",
        "tier": tier,
        "complexity": norm_complexity,
        "domain": norm_domain,
        "turns": [
            {
                "user": user_prompt,
                "assertions": {
                    "response_not_empty": True,
                    "no_error": True,
                    "no_timeout_text": True,
                },
                "expected_patterns": [],
                "forbidden_patterns": ["statement timeout|Error executing agent|Traceback"],
            }
        ],
        "final_assertions": {},
    }


def _load_custom_queries_tsv(path: str, tier: str) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise AgentEvalConfigError(f"Custom queries TSV not found: {path}")

    scenarios: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for idx, row in enumerate(reader, start=1):
            if not row:
                continue
            if row[0].strip().startswith("#"):
                continue
            if len(row) < 3:
                raise AgentEvalConfigError(
                    f"Custom queries line {idx}: expected 3 tab-separated columns "
                    "(complexity<TAB>domain<TAB>prompt)"
                )
            scenarios.append(_custom_scenario_from_row(idx, row[0], row[1], row[2], tier, id_prefix="custom"))
    return scenarios


def _load_user_queries_json(path: str, tier: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise AgentEvalConfigError("tests/scenarios/user_queries.json must contain a JSON array")

    scenarios: List[Dict[str, Any]] = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise AgentEvalConfigError("user_queries.json entries must be objects")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        scenarios.append(
            _custom_scenario_from_row(
                line_number=idx,
                complexity=str(item.get("complexity") or "simple"),
                domain=str(item.get("domain") or "traffic"),
                prompt=prompt,
                tier=tier,
                id_prefix="user_query",
            )
        )
    return scenarios


def load_agent_scenarios(config: EvalRuntimeConfig) -> List[Dict[str, Any]]:
    payload = _read_json(config.scenario_file)
    if not isinstance(payload, list):
        raise AgentEvalConfigError(f"{config.scenario_file} must contain a JSON array")

    scenarios = [validate_scenario(raw, default_tier=config.tier) for raw in payload]
    if config.include_user_queries:
        scenarios.extend(_load_user_queries_json("tests/scenarios/user_queries.json", config.tier))
    if config.queries_file:
        scenarios.extend(_load_custom_queries_tsv(config.queries_file, config.tier))

    seen_ids = set()
    for scenario in scenarios:
        sid = scenario["id"]
        if sid in seen_ids:
            raise AgentEvalConfigError(f"Duplicate scenario id detected: {sid}")
        seen_ids.add(sid)
    return scenarios


def _fetch_auth_token(config: EvalRuntimeConfig) -> str:
    if not config.auth_email or not config.auth_password:
        return ""
    login_url = f"{config.base_url.rstrip('/')}/{config.auth_login_path.lstrip('/')}"
    try:
        resp = httpx.post(
            login_url,
            json={"email": config.auth_email, "password": config.auth_password},
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        raise RuntimeError(f"Auth auto-login failed at {login_url}: {exc}") from exc
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str((resp.json() or {}).get("detail") or "").strip()
        except Exception:
            detail = ""
        suffix = f" detail={detail}" if detail else ""
        raise RuntimeError(f"Auth auto-login failed at {login_url} status={resp.status_code}.{suffix}")
    try:
        token = str((resp.json() or {}).get("token") or "").strip()
    except Exception as exc:
        raise RuntimeError(f"Auth auto-login response was not valid JSON at {login_url}") from exc
    if not token:
        raise RuntimeError(f"Auth auto-login at {login_url} returned no token.")
    return token


def make_http_client(config: EvalRuntimeConfig) -> httpx.Client:
    headers: Dict[str, str] = {}
    token = config.auth_token or _fetch_auth_token(config)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if config.auth_cookie:
        headers["Cookie"] = config.auth_cookie
    return httpx.Client(timeout=config.timeout_seconds, headers=headers)


def ensure_api_accessible(client: httpx.Client, base_url: str) -> None:
    probe_url = f"{base_url.rstrip('/')}/api/capabilities"
    try:
        resp = client.get(probe_url)
    except Exception as exc:
        raise RuntimeError(
            f"Eval API not reachable at {base_url}: {exc}. Set EVAL_BASE_URL to a reachable endpoint."
        ) from exc
    if resp.status_code >= 500:
        raise RuntimeError(f"Eval API unhealthy at {base_url} (status={resp.status_code}).")


def ensure_chat_accessible(client: httpx.Client, base_url: str, has_auth_credential: bool) -> None:
    chat_url = f"{base_url.rstrip('/')}/api/chat"
    try:
        resp = client.post(
            chat_url,
            json={"message": "__agent_eval_auth_probe__", "sessionId": "agent_eval_probe"},
        )
    except Exception as exc:
        raise RuntimeError(
            f"Eval chat endpoint not reachable at {chat_url}: {exc}. "
            "Set EVAL_BASE_URL to a reachable endpoint."
        ) from exc

    if resp.status_code == 401:
        if has_auth_credential:
            raise RuntimeError(
                "401 Unauthorized with provided auth credential (EVAL_AUTH_TOKEN/EVAL_AUTH_COOKIE). "
                "Credential is likely expired or invalid."
            )
        raise RuntimeError(
            "401 Unauthorized from /api/chat and no auth credential was provided. "
            "Set EVAL_AUTH_TOKEN (preferred) or EVAL_AUTH_COOKIE for protected deployments."
        )
    if resp.status_code >= 500:
        raise RuntimeError(f"Eval chat endpoint unhealthy at {chat_url} (status={resp.status_code}).")


def _extract_response_text(payload: Dict[str, Any]) -> str:
    return str(payload.get("responseText") or payload.get("response") or "").strip()


def _extract_agent_run_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("agentRunMetadata")
    return value if isinstance(value, dict) else {}


def _contains_number(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _detect_clarification(text: str) -> bool:
    normalized = text or ""
    return bool(CLARIFICATION_RE.search(normalized) or ("?" in normalized and len(normalized) < 280))


def _matches_patterns(text: str, patterns: Iterable[str], mode: str = "any") -> bool:
    pattern_list = [p for p in patterns if p]
    if not pattern_list:
        return True
    matches = [bool(re.search(pattern, text, re.IGNORECASE)) for pattern in pattern_list]
    if mode == "all":
        return all(matches)
    return any(matches)


def _evaluate_turn(
    *,
    scenario_id: str,
    turn_index: int,
    turn_spec: Dict[str, Any],
    turn_payload: Dict[str, Any],
    strict_mode: bool,
) -> tuple[List[str], List[str]]:
    failures: List[str] = []
    warnings: List[str] = []

    assertions = dict(turn_spec.get("assertions") or {})
    response_text = _extract_response_text(turn_payload)
    has_map = isinstance(turn_payload.get("mapSelection"), dict)
    has_error_text = bool(ERROR_TEXT_RE.search(response_text))
    has_timeout_text = bool(TIMEOUT_TEXT_RE.search(response_text))
    has_clarification = _detect_clarification(response_text)

    if assertions.get("response_not_empty", True) and not response_text:
        failures.append("response_text is empty")

    if assertions.get("no_error", True) and has_error_text:
        failures.append("response_text contains error-like language")

    if "has_map" in assertions and bool(assertions.get("has_map")) != has_map:
        failures.append(f"has_map expected={bool(assertions.get('has_map'))} actual={has_map}")

    if assertions.get("no_timeout_text", False) and has_timeout_text:
        failures.append("response_text contains timeout-like language")

    if "has_clarification" in assertions and bool(assertions.get("has_clarification")) != has_clarification:
        failures.append(
            f"has_clarification expected={bool(assertions.get('has_clarification'))} actual={has_clarification}"
        )

    if assertions.get("contains_number", False) and not _contains_number(response_text):
        failures.append("response_text missing numeric signal")

    expected_patterns = turn_spec.get("expected_patterns") or []
    forbidden_patterns = turn_spec.get("forbidden_patterns") or []
    expected_pattern_mode = str(assertions.get("expected_pattern_mode") or "any").lower()
    expected_required = bool(assertions.get("expected_patterns_required", False) or (strict_mode and expected_patterns))

    if expected_patterns:
        matched = _matches_patterns(response_text, expected_patterns, expected_pattern_mode)
        if expected_required and not matched:
            failures.append(f"expected_patterns not matched (mode={expected_pattern_mode})")
        elif not matched:
            warnings.append("expected_patterns not matched (advisory)")

    for pattern in forbidden_patterns:
        if re.search(pattern, response_text, re.IGNORECASE):
            failures.append(f"forbidden pattern matched: {pattern}")

    if int(turn_payload.get("_status_code", 200)) >= 400:
        failures.append(f"http status={turn_payload.get('_status_code')}")

    if turn_payload.get("_auth_error"):
        failures.append(str(turn_payload.get("_auth_error")))

    if failures:
        prefix = f"turn#{turn_index + 1}"
        failures = [f"{prefix}: {msg}" for msg in failures]
    if warnings:
        prefix = f"turn#{turn_index + 1}"
        warnings = [f"{prefix}: {msg}" for msg in warnings]
    return failures, warnings


def send_chat_turn(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    user_prompt: str,
    has_auth_credential: bool,
) -> Dict[str, Any]:
    chat_url = f"{base_url.rstrip('/')}/api/chat"
    t0 = time.time()
    try:
        resp = client.post(chat_url, json={"message": user_prompt, "sessionId": session_id})
        elapsed_ms = int((time.time() - t0) * 1000)
    except Exception as exc:
        elapsed_ms = int((time.time() - t0) * 1000)
        error_text = f"{type(exc).__name__}: {exc}".strip()
        return {
            "responseText": "",
            "_elapsed_ms": elapsed_ms,
            "_status_code": 0,
            "_transport_error": error_text,
        }

    payload: Dict[str, Any] = {}
    try:
        payload = resp.json()
    except Exception:
        payload = {"responseText": resp.text}

    payload["_elapsed_ms"] = elapsed_ms
    payload["_status_code"] = resp.status_code
    if resp.status_code == 401:
        if has_auth_credential:
            payload["_auth_error"] = (
                "401 Unauthorized with provided auth credential "
                "(EVAL_AUTH_TOKEN/EVAL_AUTH_COOKIE likely expired/invalid)"
            )
        else:
            payload["_auth_error"] = (
                "401 Unauthorized. Set EVAL_AUTH_TOKEN (preferred) or EVAL_AUTH_COOKIE for protected /api/chat"
            )
    return payload


def run_agent_scenario(client: httpx.Client, config: EvalRuntimeConfig, scenario: Dict[str, Any]) -> Dict[str, Any]:
    session_id = f"agent_eval::{scenario['id']}::{uuid.uuid4().hex[:8]}"
    scenario_failures: List[str] = []
    scenario_warnings: List[str] = []
    turn_records: List[Dict[str, Any]] = []

    for idx, turn in enumerate(scenario["turns"]):
        payload = send_chat_turn(
            client,
            base_url=config.base_url,
            session_id=session_id,
            user_prompt=turn["user"],
            has_auth_credential=bool(config.auth_token or config.auth_cookie),
        )
        response_text = _extract_response_text(payload)
        has_error_text = bool(ERROR_TEXT_RE.search(response_text))
        turn_failures, turn_warnings = _evaluate_turn(
            scenario_id=scenario["id"],
            turn_index=idx,
            turn_spec=turn,
            turn_payload=payload,
            strict_mode=config.strict_mode,
        )
        transport_error = str(payload.get("_transport_error") or "").strip()
        if transport_error:
            turn_failures.append(f"turn#{idx + 1}: transport error: {transport_error}")
        scenario_failures.extend(turn_failures)
        scenario_warnings.extend(turn_warnings)
        turn_records.append(
            {
                "turn_index": idx + 1,
                "user": turn["user"],
                "response_text": response_text,
                "elapsed_ms": payload.get("_elapsed_ms", 0),
                "status_code": payload.get("_status_code"),
                "has_map": isinstance(payload.get("mapSelection"), dict),
                "has_clarification": _detect_clarification(response_text),
                "has_timeout_text": bool(
                    TIMEOUT_TEXT_RE.search(response_text) or TIMEOUT_TEXT_RE.search(transport_error)
                ),
                "has_error_text": has_error_text,
                "transport_error": transport_error or None,
                "agent_run_metadata": _extract_agent_run_metadata(payload),
                "failures": turn_failures,
                "warnings": turn_warnings,
            }
        )

    final_assertions = scenario.get("final_assertions") or {}
    if final_assertions and turn_records:
        last_turn = turn_records[-1]
        if "has_map" in final_assertions:
            expected = bool(final_assertions.get("has_map"))
            if bool(last_turn.get("has_map")) != expected:
                scenario_failures.append(
                    f"final: has_map expected={expected} actual={bool(last_turn.get('has_map'))}"
                )
        if "has_clarification" in final_assertions:
            expected = bool(final_assertions.get("has_clarification"))
            if bool(last_turn.get("has_clarification")) != expected:
                scenario_failures.append(
                    f"final: has_clarification expected={expected} actual={bool(last_turn.get('has_clarification'))}"
                )
        if "no_timeout_text" in final_assertions and bool(final_assertions.get("no_timeout_text")):
            if bool(last_turn.get("has_timeout_text")):
                scenario_failures.append("final: response_text contains timeout-like language")

    total_elapsed_ms = int(sum(int(turn.get("elapsed_ms") or 0) for turn in turn_records))
    scenario_passed = not scenario_failures
    return {
        "id": scenario["id"],
        "tier": scenario["tier"],
        "domain": scenario["domain"],
        "complexity": scenario["complexity"],
        "session_id": session_id,
        "passed": scenario_passed,
        "failures": scenario_failures,
        "warnings": scenario_warnings,
        "turns": turn_records,
        "total_elapsed_ms": total_elapsed_ms,
        "has_map_any": any(bool(turn.get("has_map")) for turn in turn_records),
        "has_clarification_any": any(bool(turn.get("has_clarification")) for turn in turn_records),
        "timeout_or_error_text_any": any(
            bool(turn.get("has_timeout_text")) or bool(turn.get("has_error_text")) for turn in turn_records
        ),
    }


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * percentile))))
    return float(sorted_values[index])


def summarize_results(results: List[Dict[str, Any]], config: EvalRuntimeConfig) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for row in results if row.get("passed"))
    failed = total - passed
    latencies = [float(row.get("total_elapsed_ms") or 0.0) for row in results]
    has_map_count = sum(1 for row in results if row.get("has_map_any"))
    clarification_count = sum(1 for row in results if row.get("has_clarification_any"))
    timeout_or_error_count = sum(1 for row in results if row.get("timeout_or_error_text_any"))
    usage_roles: Dict[str, Dict[str, Any]] = {}
    routing_roles: Dict[str, Dict[str, Any]] = {}
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cached_prompt_tokens = 0
    total_estimated_cost_usd = 0.0
    has_estimated_cost = False

    def _merge_role_summary(store: Dict[str, Dict[str, Any]], item: Dict[str, Any]) -> None:
        role = str(item.get("role") or "unknown").strip() or "unknown"
        bucket = store.setdefault(
            role,
            {
                "role": role,
                "backends": set(),
                "configured_models": set(),
                "providers": set(),
                "model_versions": set(),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_prompt_tokens": 0,
                "estimated_cost_usd": 0.0,
                "usage_count": 0,
            },
        )
        backend = str(item.get("backend") or "").strip()
        configured_model = str(item.get("configured_model") or "").strip()
        provider = str(item.get("provider") or "").strip()
        if backend:
            bucket["backends"].add(backend)
        if configured_model:
            bucket["configured_models"].add(configured_model)
        if provider:
            bucket["providers"].add(provider)
        for model_version in item.get("model_versions") or []:
            text = str(model_version or "").strip()
            if text:
                bucket["model_versions"].add(text)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens"):
            try:
                bucket[key] += int(item.get(key) or 0)
            except (TypeError, ValueError):
                continue
        raw_cost = item.get("estimated_cost_usd")
        if raw_cost not in (None, ""):
            try:
                bucket["estimated_cost_usd"] += float(raw_cost)
            except (TypeError, ValueError):
                pass
        bucket["usage_count"] += 1

    by_domain: Dict[str, Dict[str, Any]] = {}
    for row in results:
        domain = str(row.get("domain") or "unknown")
        bucket = by_domain.setdefault(domain, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        if row.get("passed"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
        for turn in row.get("turns", []) or []:
            metadata = turn.get("agent_run_metadata") if isinstance(turn, dict) else {}
            if not isinstance(metadata, dict):
                continue
            usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
            try:
                total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
                total_completion_tokens += int(usage.get("completion_tokens") or 0)
                total_tokens += int(usage.get("total_tokens") or 0)
                total_cached_prompt_tokens += int(usage.get("cached_prompt_tokens") or 0)
            except (TypeError, ValueError):
                pass
            raw_cost = metadata.get("estimated_cost_usd")
            if raw_cost not in (None, ""):
                try:
                    total_estimated_cost_usd += float(raw_cost)
                    has_estimated_cost = True
                except (TypeError, ValueError):
                    pass
            for role_item in metadata.get("roles_used") or []:
                if isinstance(role_item, dict):
                    _merge_role_summary(usage_roles, role_item)
            for route_item in metadata.get("routing") or []:
                if isinstance(route_item, dict):
                    _merge_role_summary(routing_roles, route_item)

    scenario_status = []
    for row in results:
        scenario_cost = 0.0
        scenario_has_cost = False
        scenario_tokens = 0
        for turn in row.get("turns", []) or []:
            metadata = turn.get("agent_run_metadata") if isinstance(turn, dict) else {}
            if not isinstance(metadata, dict):
                continue
            usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
            try:
                scenario_tokens += int(usage.get("total_tokens") or 0)
            except (TypeError, ValueError):
                pass
            raw_cost = metadata.get("estimated_cost_usd")
            if raw_cost not in (None, ""):
                try:
                    scenario_cost += float(raw_cost)
                    scenario_has_cost = True
                except (TypeError, ValueError):
                    pass
        scenario_status.append(
            {
                "id": row.get("id"),
                "domain": row.get("domain"),
                "tier": row.get("tier"),
                "complexity": row.get("complexity"),
                "passed": bool(row.get("passed")),
                "latency_ms": int(row.get("total_elapsed_ms") or 0),
                "has_map": bool(row.get("has_map_any")),
                "has_clarification": bool(row.get("has_clarification_any")),
                "timeout_or_error_text": bool(row.get("timeout_or_error_text_any")),
                "total_tokens": scenario_tokens,
                "estimated_cost_usd": round(scenario_cost, 8) if scenario_has_cost else None,
                "failures": list(row.get("failures") or []),
            }
        )

    def _finalize_role_summaries(store: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for role in sorted(store):
            bucket = store[role]
            rows.append(
                {
                    "role": bucket["role"],
                    "backends": sorted(bucket["backends"]),
                    "configured_models": sorted(bucket["configured_models"]),
                    "providers": sorted(bucket["providers"]),
                    "model_versions": sorted(bucket["model_versions"]),
                    "prompt_tokens": int(bucket["prompt_tokens"]),
                    "completion_tokens": int(bucket["completion_tokens"]),
                    "total_tokens": int(bucket["total_tokens"]),
                    "cached_prompt_tokens": int(bucket["cached_prompt_tokens"]),
                    "estimated_cost_usd": round(float(bucket["estimated_cost_usd"]), 8)
                    if bucket["estimated_cost_usd"]
                    else None,
                    "usage_count": int(bucket["usage_count"]),
                }
            )
        return rows

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tier": config.tier,
        "strict_mode": bool(config.strict_mode),
        "base_url": config.base_url,
        "totals": {
            "scenarios": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round((passed / total) if total else 0.0, 4),
        },
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
        },
        "rates": {
            "has_map_rate": round((has_map_count / total) if total else 0.0, 4),
            "clarification_rate": round((clarification_count / total) if total else 0.0, 4),
            "timeout_or_error_text_rate": round((timeout_or_error_count / total) if total else 0.0, 4),
        },
        "llm_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "cached_prompt_tokens": total_cached_prompt_tokens,
            "estimated_cost_usd": round(total_estimated_cost_usd, 8) if has_estimated_cost else None,
            "estimated_cost_per_successful_scenario_usd": (
                round(total_estimated_cost_usd / passed, 8) if has_estimated_cost and passed else None
            ),
            "roles_used": _finalize_role_summaries(usage_roles),
            "routing_roles": _finalize_role_summaries(routing_roles),
        },
        "by_domain": by_domain,
        "scenario_status": scenario_status,
    }
    return summary


def write_eval_artifacts(results: List[Dict[str, Any]], config: EvalRuntimeConfig) -> Dict[str, Any]:
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_results_path = out_dir / "raw_results.json"
    summary_path = out_dir / "summary.json"
    summary_md_path = out_dir / "summary.md"

    summary = summarize_results(results, config)

    with raw_results_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with summary_md_path.open("w", encoding="utf-8") as f:
        f.write(render_summary_markdown(summary))

    return {
        "raw_results_path": str(raw_results_path),
        "summary_path": str(summary_path),
        "summary_md_path": str(summary_md_path),
    }


def render_summary_markdown(summary: Dict[str, Any]) -> str:
    totals = summary.get("totals", {})
    latency = summary.get("latency_ms", {})
    rates = summary.get("rates", {})
    llm_usage = summary.get("llm_usage", {})
    by_domain = summary.get("by_domain", {})

    lines = [
        "# Agent Eval Summary",
        "",
        f"- Timestamp: `{summary.get('timestamp')}`",
        f"- Tier: `{summary.get('tier')}`",
        f"- Base URL: `{summary.get('base_url')}`",
        f"- Strict Mode: `{summary.get('strict_mode')}`",
        "",
        "## Totals",
        "",
        f"- Scenarios: `{totals.get('scenarios', 0)}`",
        f"- Passed: `{totals.get('passed', 0)}`",
        f"- Failed: `{totals.get('failed', 0)}`",
        f"- Pass Rate: `{totals.get('pass_rate', 0.0):.2%}`",
        "",
        "## Latency (ms)",
        "",
        f"- avg: `{latency.get('avg', 0.0)}`",
        f"- p50: `{latency.get('p50', 0.0)}`",
        f"- p95: `{latency.get('p95', 0.0)}`",
        "",
        "## Rates",
        "",
        f"- has_map: `{rates.get('has_map_rate', 0.0):.2%}`",
        f"- clarification: `{rates.get('clarification_rate', 0.0):.2%}`",
        f"- timeout/error text: `{rates.get('timeout_or_error_text_rate', 0.0):.2%}`",
        "",
        "## LLM Usage",
        "",
        f"- prompt_tokens: `{llm_usage.get('prompt_tokens', 0)}`",
        f"- completion_tokens: `{llm_usage.get('completion_tokens', 0)}`",
        f"- total_tokens: `{llm_usage.get('total_tokens', 0)}`",
        f"- estimated_cost_usd: `{llm_usage.get('estimated_cost_usd')}`",
        f"- estimated_cost_per_successful_scenario_usd: `{llm_usage.get('estimated_cost_per_successful_scenario_usd')}`",
        "",
        "## By Domain",
        "",
    ]
    usage_roles = llm_usage.get("roles_used") or []
    if usage_roles:
        lines.extend(["## Models Used", ""])
        for row in usage_roles:
            model_text = ", ".join(row.get("configured_models") or []) or "unknown"
            backend_text = ", ".join(row.get("backends") or []) or "unknown"
            lines.append(
                f"- `{row.get('role')}`: backend={backend_text}; model={model_text}; "
                f"tokens={row.get('total_tokens', 0)}; cost={row.get('estimated_cost_usd')}"
            )
        lines.append("")
    for domain, values in sorted(by_domain.items()):
        lines.append(
            f"- `{domain}`: {values.get('passed', 0)}/{values.get('total', 0)} passed "
            f"(failed={values.get('failed', 0)})"
        )

    failed_rows = [row for row in summary.get("scenario_status", []) if not row.get("passed")]
    if failed_rows:
        lines.extend(["", "## Failed Scenarios", ""])
        for row in failed_rows:
            lines.append(f"- `{row.get('id')}` ({row.get('domain')}): {', '.join(row.get('failures') or [])}")
    return "\n".join(lines) + "\n"

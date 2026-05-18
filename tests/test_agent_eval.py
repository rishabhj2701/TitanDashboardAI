"""Dedicated agent regression suite for production-like /api/chat evaluations."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import pytest

from tests.agent_eval_support import (
    AgentEvalConfigError,
    build_runtime_config,
    ensure_api_accessible,
    ensure_chat_accessible,
    load_agent_scenarios,
    make_http_client,
    run_agent_scenario,
    write_eval_artifacts,
)

if str(os.environ.get("RUN_AGENT_EVAL", "0")).strip().lower() not in {"1", "true", "yes"}:
    pytest.skip(
        "Agent eval is opt-in. Set RUN_AGENT_EVAL=1 to run tests/test_agent_eval.py.",
        allow_module_level=True,
    )

try:
    CONFIG = build_runtime_config()
    SCENARIOS = load_agent_scenarios(CONFIG)
except AgentEvalConfigError as exc:
    pytest.fail(f"Agent eval configuration error: {exc}")

if not SCENARIOS:
    pytest.skip("No agent eval scenarios loaded.", allow_module_level=True)

RUN_RESULTS: List[Dict[str, Any]] = []


@pytest.fixture(scope="session")
def eval_runtime():
    client = None
    try:
        client = make_http_client(CONFIG)
        ensure_api_accessible(client, CONFIG.base_url)
        ensure_chat_accessible(
            client,
            CONFIG.base_url,
            has_auth_credential=bool(
                CONFIG.auth_token
                or CONFIG.auth_cookie
                or (CONFIG.auth_email and CONFIG.auth_password)
            ),
        )
    except RuntimeError as exc:
        if client is not None:
            client.close()
        pytest.exit(f"Agent eval preflight failed: {exc}", returncode=2)

    yield {"client": client, "config": CONFIG}

    artifacts = write_eval_artifacts(RUN_RESULTS, CONFIG)
    client.close()
    print(
        "Agent eval artifacts written: "
        f"{artifacts['raw_results_path']}, {artifacts['summary_path']}, {artifacts['summary_md_path']}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_agent_scenario(eval_runtime, scenario):
    result = run_agent_scenario(
        client=eval_runtime["client"],
        config=eval_runtime["config"],
        scenario=scenario,
    )
    RUN_RESULTS.append(result)
    if not result["passed"]:
        turn_dump = "\n".join(
            f"  turn#{turn['turn_index']} user={turn['user']!r} response={turn['response_text'][:280]!r}"
            for turn in result.get("turns", [])
        )
        pytest.fail(
            f"Scenario failed: {result['id']}\n"
            f"Failures: {result.get('failures', [])}\n"
            f"Warnings: {result.get('warnings', [])}\n"
            f"Turns:\n{turn_dump}"
        )

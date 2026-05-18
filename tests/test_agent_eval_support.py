"""Unit tests for agent eval support utilities."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.agent_eval_support import (
    AgentEvalConfigError,
    EvalRuntimeConfig,
    load_agent_scenarios,
    make_http_client,
    run_agent_scenario,
    send_chat_turn,
    summarize_results,
    validate_scenario,
)


def test_validate_scenario_accepts_single_and_multi_turn() -> None:
    single = {
        "id": "s1",
        "tier": "smoke",
        "complexity": "simple",
        "domain": "traffic",
        "turns": [{"user": "Show I-70", "assertions": {"no_error": True}}],
        "final_assertions": {"has_map": True},
    }
    multi = {
        "id": "m1",
        "tier": "deep",
        "complexity": "in_depth",
        "domain": "traffic",
        "turns": [
            {"user": "Show I-70", "assertions": {"no_error": True}},
            {"user": "Now I-44", "assertions": {"no_error": True}},
        ],
        "final_assertions": {},
    }
    validated_single = validate_scenario(single, default_tier="smoke")
    validated_multi = validate_scenario(multi, default_tier="deep")
    assert validated_single["id"] == "s1"
    assert len(validated_single["turns"]) == 1
    assert validated_multi["id"] == "m1"
    assert len(validated_multi["turns"]) == 2


def test_validate_scenario_rejects_bad_tier_and_assertion() -> None:
    bad_tier = {
        "id": "bad_tier",
        "tier": "nightly",
        "complexity": "simple",
        "domain": "traffic",
        "turns": [{"user": "Show I-70"}],
    }
    with pytest.raises(AgentEvalConfigError):
        validate_scenario(bad_tier, default_tier="smoke")

    bad_assertion = {
        "id": "bad_assertion",
        "tier": "smoke",
        "complexity": "simple",
        "domain": "traffic",
        "turns": [{"user": "Show I-70", "assertions": {"unknown_key": True}}],
    }
    with pytest.raises(AgentEvalConfigError):
        validate_scenario(bad_assertion, default_tier="smoke")


def test_make_http_client_includes_cookie_header() -> None:
    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="session=abc123",
        auth_token="",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    client = make_http_client(cfg)
    try:
        assert client.headers.get("Cookie") == "session=abc123"
    finally:
        client.close()


def test_make_http_client_includes_bearer_token_header() -> None:
    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="jwt-token-value",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    client = make_http_client(cfg)
    try:
        assert client.headers.get("Authorization") == "Bearer jwt-token-value"
    finally:
        client.close()


def test_make_http_client_auto_logins_with_email_password(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"token": "auto-token-123"}

    def _fake_post(url, json, timeout):  # noqa: ANN001
        assert url == "http://localhost:8000/api/auth/login"
        assert json["email"] == "test@example.com"
        assert json["password"] == "secret123"
        return _Resp()

    monkeypatch.setattr("tests.agent_eval_support.httpx.post", _fake_post)

    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="",
        auth_email="test@example.com",
        auth_password="secret123",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    client = make_http_client(cfg)
    try:
        assert client.headers.get("Authorization") == "Bearer auto-token-123"
    finally:
        client.close()


def test_load_agent_scenarios_appends_custom_queries(tmp_path: Path) -> None:
    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(
        json.dumps(
            [
                {
                    "id": "base_1",
                    "tier": "smoke",
                    "complexity": "simple",
                    "domain": "traffic",
                    "turns": [{"user": "Show I-70", "assertions": {"no_error": True}}],
                    "final_assertions": {},
                }
            ]
        ),
        encoding="utf-8",
    )

    queries_file = tmp_path / "queries.tsv"
    queries_file.write_text(
        "\n".join(
            [
                "simple\ttraffic\tShow just I-70 data",
                "in_depth\ttraffic\tShow I-70 traffic data for active run then switch to I-44",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file=str(scenario_file),
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file=str(queries_file),
        timeout_seconds=5,
    )

    loaded = load_agent_scenarios(cfg)
    loaded_ids = [row["id"] for row in loaded]
    assert loaded_ids == ["base_1", "custom_1", "custom_2"]


def test_send_chat_turn_returns_structured_transport_error() -> None:
    class _Client:
        def post(self, *_args, **_kwargs):  # noqa: ANN001
            raise httpx.ReadTimeout("timed out")

    payload = send_chat_turn(
        client=_Client(),  # type: ignore[arg-type]
        base_url="http://localhost:8000",
        session_id="s1",
        user_prompt="Show I-70",
        has_auth_credential=True,
    )
    assert payload["_status_code"] == 0
    assert "ReadTimeout" in str(payload["_transport_error"])
    assert payload["responseText"] == ""


def test_run_agent_scenario_records_transport_error_failure() -> None:
    class _Client:
        def post(self, *_args, **_kwargs):  # noqa: ANN001
            raise httpx.ReadTimeout("timed out")

    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="jwt-token-value",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    scenario = {
        "id": "timeout_case",
        "tier": "smoke",
        "complexity": "simple",
        "domain": "traffic",
        "turns": [{"user": "Show I-70", "assertions": {"response_not_empty": True, "no_timeout_text": True}}],
        "final_assertions": {},
    }

    result = run_agent_scenario(_Client(), cfg, scenario)  # type: ignore[arg-type]
    assert result["passed"] is False
    assert any("transport error" in failure for failure in result["failures"])
    assert result["timeout_or_error_text_any"] is True
    assert result["turns"][0]["transport_error"]


def test_summarize_results_includes_transport_error_failure() -> None:
    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="jwt-token-value",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    results = [
        {
            "id": "timeout_case",
            "tier": "smoke",
            "domain": "traffic",
            "complexity": "simple",
            "passed": False,
            "failures": ["turn#1: transport error: ReadTimeout: timed out"],
            "warnings": [],
            "turns": [
                {
                    "turn_index": 1,
                    "user": "Show I-70",
                    "response_text": "",
                    "elapsed_ms": 120000,
                    "status_code": 0,
                    "has_map": False,
                    "has_clarification": False,
                    "has_timeout_text": True,
                    "has_error_text": False,
                    "transport_error": "ReadTimeout: timed out",
                    "failures": ["turn#1: transport error: ReadTimeout: timed out"],
                    "warnings": [],
                }
            ],
            "total_elapsed_ms": 120000,
            "has_map_any": False,
            "has_clarification_any": False,
            "timeout_or_error_text_any": True,
        }
    ]

    summary = summarize_results(results, cfg)
    assert summary["totals"]["failed"] == 1
    assert summary["scenario_status"][0]["id"] == "timeout_case"
    assert summary["scenario_status"][0]["timeout_or_error_text"] is True


def test_summarize_results_includes_llm_usage_and_cost() -> None:
    cfg = EvalRuntimeConfig(
        tier="smoke",
        scenario_file="tests/scenarios/agent_smoke.json",
        output_dir="artifacts/evals/test",
        base_url="http://localhost:8000",
        auth_cookie="",
        auth_token="jwt-token-value",
        auth_email="",
        auth_password="",
        auth_login_path="/api/auth/login",
        strict_mode=False,
        include_user_queries=False,
        queries_file="",
        timeout_seconds=5,
    )
    results = [
        {
            "id": "usage_case",
            "tier": "smoke",
            "domain": "traffic",
            "complexity": "simple",
            "passed": True,
            "failures": [],
            "warnings": [],
            "turns": [
                {
                    "turn_index": 1,
                    "user": "Show I-70",
                    "response_text": "ok",
                    "elapsed_ms": 800,
                    "status_code": 200,
                    "has_map": True,
                    "has_clarification": False,
                    "has_timeout_text": False,
                    "has_error_text": False,
                    "transport_error": None,
                    "agent_run_metadata": {
                        "routing": [
                            {
                                "role": "dispatcher",
                                "backend": "litellm",
                                "configured_model": "openrouter/openai/gpt-5-mini",
                                "provider": "openrouter",
                            }
                        ],
                        "roles_used": [
                            {
                                "role": "dispatcher",
                                "backend": "litellm",
                                "configured_model": "openrouter/openai/gpt-5-mini",
                                "provider": "openrouter",
                                "model_versions": ["openai/gpt-5-mini"],
                                "prompt_tokens": 120,
                                "completion_tokens": 30,
                                "total_tokens": 150,
                                "cached_prompt_tokens": 0,
                                "estimated_cost_usd": 0.0125,
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 120,
                            "completion_tokens": 30,
                            "total_tokens": 150,
                            "cached_prompt_tokens": 0,
                        },
                        "estimated_cost_usd": 0.0125,
                    },
                    "failures": [],
                    "warnings": [],
                }
            ],
            "total_elapsed_ms": 800,
            "has_map_any": True,
            "has_clarification_any": False,
            "timeout_or_error_text_any": False,
        }
    ]

    summary = summarize_results(results, cfg)
    assert summary["llm_usage"]["prompt_tokens"] == 120
    assert summary["llm_usage"]["completion_tokens"] == 30
    assert summary["llm_usage"]["total_tokens"] == 150
    assert summary["llm_usage"]["estimated_cost_usd"] == 0.0125
    assert summary["llm_usage"]["estimated_cost_per_successful_scenario_usd"] == 0.0125
    assert summary["llm_usage"]["roles_used"][0]["configured_models"] == ["openrouter/openai/gpt-5-mini"]
    assert summary["scenario_status"][0]["estimated_cost_usd"] == 0.0125

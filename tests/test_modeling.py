from __future__ import annotations

from dynamic_analyst.modeling import (
    aggregate_usage_events,
    estimate_cost_usd,
    finalize_usage_collector,
    get_model_runtime_config,
)


def test_get_model_runtime_config_defaults_to_google(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_BACKEND", raising=False)
    monkeypatch.delenv("AGENT_MODEL_DEFAULT", raising=False)

    config = get_model_runtime_config("dispatcher")

    assert config["backend"] == "google"
    assert config["configured_model"] == "gemini-2.5-flash"
    assert config["provider"] == "google"


def test_get_model_runtime_config_supports_role_override_and_openrouter(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_BACKEND", "openrouter")
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "openrouter/openai/gpt-5-mini")
    monkeypatch.setenv("AGENT_MODEL_REACT", "openrouter/anthropic/claude-sonnet-4")
    monkeypatch.setenv("OPENROUTER_HTTP_REFERER", "https://example.com")
    monkeypatch.setenv("OPENROUTER_X_TITLE", "Traffic Analyst")

    config = get_model_runtime_config("react")

    assert config["backend"] == "litellm"
    assert config["configured_model"] == "openrouter/anthropic/claude-sonnet-4"
    assert config["provider"] == "openrouter"
    assert config["litellm_kwargs"]["extra_headers"]["HTTP-Referer"] == "https://example.com"
    assert config["litellm_kwargs"]["extra_headers"]["X-OpenRouter-Title"] == "Traffic Analyst"


def test_estimate_cost_usd_uses_builtin_price_book():
    cost_usd, pricing_source = estimate_cost_usd(
        configured_model="gemini-2.5-flash",
        observed_model_versions=["gemini-2.5-flash"],
        prompt_tokens=1000,
        completion_tokens=500,
    )

    assert cost_usd is not None
    assert pricing_source in {"builtin", "mixed"}


def test_aggregate_usage_events_rolls_up_roles():
    events = [
        {
            "type": "llm_usage",
            "role": "dispatcher",
            "backend": "google",
            "configured_model": "gemini-2.5-flash",
            "provider": "google",
            "model_versions": ["gemini-2.5-flash"],
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cached_prompt_tokens": 0,
            "estimated_cost_usd": 0.00011,
            "pricing_source": "builtin",
        },
        {
            "type": "llm_usage",
            "role": "react",
            "backend": "litellm",
            "configured_model": "openrouter/openai/gpt-5-mini",
            "provider": "openrouter",
            "model_versions": ["openai/gpt-5-mini"],
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
            "cached_prompt_tokens": 0,
            "estimated_cost_usd": 0.0125,
            "pricing_source": "env",
        },
    ]

    aggregated = aggregate_usage_events(events)

    assert aggregated["usage"]["total_tokens"] == 400
    assert aggregated["estimated_cost_usd"] == 0.01261
    assert {row["role"] for row in aggregated["roles_used"]} == {"dispatcher", "react"}


def test_finalize_usage_collector_returns_none_without_usage():
    assert finalize_usage_collector({"role": "dispatcher"}) is None

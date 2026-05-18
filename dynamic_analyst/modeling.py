from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
DEFAULT_LITELLM_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ROLE_ENV_ALIASES = {
    "dispatcher": "DISPATCHER",
    "react": "REACT",
    "planner": "PLANNER",
    "explainer": "EXPLAINER",
}
BUILTIN_PRICE_BOOK_USD_PER_1M = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_role_name(role: str) -> str:
    raw = str(role or "").strip().lower()
    if not raw:
        return "default"
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "default"


def _role_env_name(role: str) -> str:
    normalized = _normalize_role_name(role)
    return ROLE_ENV_ALIASES.get(normalized, normalized.upper())


def _normalize_backend(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in {"", "google", "gemini"}:
        return "google"
    if lowered in {"litellm", "lite_llm", "openrouter"}:
        return "litellm"
    return lowered


def _safe_float_env(name: str) -> float | None:
    raw = _env(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _safe_json_dict_env(name: str) -> dict[str, Any]:
    raw = _env(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _provider_hint(model_name: str, backend: str) -> str:
    normalized = str(model_name or "").strip()
    if normalized.startswith("openrouter/"):
        return "openrouter"
    if "/" in normalized:
        return normalized.split("/", 1)[0].strip().lower()
    if backend == "google":
        return "google"
    return ""


def _litellm_kwargs(configured_model: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    api_base = _env("AGENT_LITELLM_API_BASE")
    if api_base:
        kwargs["api_base"] = api_base
    elif configured_model.startswith("openrouter/") and _env("OPENROUTER_USE_OPENAI_API", "0") in {"1", "true", "yes", "on"}:
        kwargs["api_base"] = DEFAULT_LITELLM_OPENROUTER_BASE_URL

    api_key = _env("AGENT_LITELLM_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key

    extra_headers = _safe_json_dict_env("AGENT_LITELLM_EXTRA_HEADERS_JSON")
    openrouter_referer = _env("OPENROUTER_HTTP_REFERER")
    openrouter_title = _env("OPENROUTER_X_TITLE") or _env("OPENROUTER_APP_TITLE")
    if openrouter_referer:
        extra_headers.setdefault("HTTP-Referer", openrouter_referer)
    if openrouter_title:
        extra_headers.setdefault("X-OpenRouter-Title", openrouter_title)
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    temperature = _safe_float_env("AGENT_LITELLM_TEMPERATURE")
    if temperature is not None:
        kwargs["temperature"] = temperature

    timeout = _safe_float_env("AGENT_LITELLM_TIMEOUT_SECONDS")
    if timeout is not None:
        kwargs["timeout"] = timeout

    reasoning_effort = _env("AGENT_LITELLM_REASONING_EFFORT")
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort

    if _is_truthy(_env("AGENT_LITELLM_DROP_PARAMS")):
        kwargs["drop_params"] = True

    return kwargs


def get_model_runtime_config(role: str) -> dict[str, Any]:
    normalized_role = _normalize_role_name(role)
    backend_raw = _env("AGENT_MODEL_BACKEND", "google")
    backend = _normalize_backend(backend_raw)
    configured_model = (
        _env(f"AGENT_MODEL_{_role_env_name(normalized_role)}")
        or _env("AGENT_MODEL_DEFAULT", DEFAULT_AGENT_MODEL)
        or DEFAULT_AGENT_MODEL
    )
    config: dict[str, Any] = {
        "role": normalized_role,
        "backend": backend,
        "backend_raw": backend_raw or backend,
        "configured_model": configured_model,
        "provider": _provider_hint(configured_model, backend),
    }
    if backend == "litellm":
        config["litellm_kwargs"] = _litellm_kwargs(configured_model)
    return config


def get_agent_model(role: str) -> Any:
    config = get_model_runtime_config(role)
    if config["backend"] == "google":
        return config["configured_model"]
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - exercised only in LiteLLM mode
        raise RuntimeError(
            "AGENT_MODEL_BACKEND=litellm requires LiteLLM support. "
            "Install the repo dependencies with 'litellm' available."
        ) from exc
    return LiteLlm(model=config["configured_model"], **dict(config.get("litellm_kwargs") or {}))


def get_model_routing_overview(roles: list[str] | None = None) -> list[dict[str, Any]]:
    role_names = roles or ["dispatcher", "react", "planner", "explainer"]
    overview: list[dict[str, Any]] = []
    for role in role_names:
        config = get_model_runtime_config(role)
        row = {
            "role": config["role"],
            "backend": config["backend"],
            "configured_model": config["configured_model"],
            "provider": config.get("provider") or None,
        }
        kwargs = dict(config.get("litellm_kwargs") or {})
        if kwargs:
            row["litellm_options"] = sorted(kwargs.keys())
        overview.append(row)
    return overview


def new_usage_collector(role: str) -> dict[str, Any]:
    config = get_model_runtime_config(role)
    return {
        "role": config["role"],
        "backend": config["backend"],
        "configured_model": config["configured_model"],
        "provider": config.get("provider") or "",
        "model_versions": [],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
    }


def _usage_value(usage: Any, attr_name: str, dict_key: str) -> int:
    if usage is None:
        return 0
    value = getattr(usage, attr_name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(dict_key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def collect_usage_from_event(collector: dict[str, Any], event: Any) -> None:
    model_version = str(getattr(event, "model_version", None) or "").strip()
    if model_version and model_version not in collector["model_versions"]:
        collector["model_versions"].append(model_version)

    usage = getattr(event, "usage_metadata", None)
    collector["prompt_tokens"] += _usage_value(usage, "prompt_token_count", "prompt_token_count")
    collector["completion_tokens"] += _usage_value(usage, "candidates_token_count", "candidates_token_count")
    collector["total_tokens"] += _usage_value(usage, "total_token_count", "total_token_count")
    collector["cached_prompt_tokens"] += _usage_value(
        usage,
        "cached_content_token_count",
        "cached_content_token_count",
    )


def _candidate_price_keys(*model_names: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for raw_name in model_names:
        name = str(raw_name or "").strip().lower()
        if not name:
            continue
        variants = [name]
        if name.startswith("openrouter/"):
            variants.append(name[len("openrouter/"):])
        if name.startswith("models/"):
            variants.append(name[len("models/"):])
        if "/" in name:
            variants.append(name.rsplit("/", 1)[-1])
        for candidate in variants:
            normalized = candidate.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                keys.append(normalized)
    return keys


def _load_env_price_book() -> dict[str, dict[str, float]]:
    raw = _safe_json_dict_env("AGENT_MODEL_PRICING_JSON")
    out: dict[str, dict[str, float]] = {}
    for model_name, value in raw.items():
        if not isinstance(value, dict):
            continue
        input_price = value.get("input") or value.get("input_per_1m") or value.get("input_per_million")
        output_price = value.get("output") or value.get("output_per_1m") or value.get("output_per_million")
        try:
            if input_price is None or output_price is None:
                continue
            out[str(model_name).strip().lower()] = {
                "input": float(input_price),
                "output": float(output_price),
            }
        except (TypeError, ValueError):
            continue
    return out


def estimate_cost_usd(
    *,
    configured_model: str,
    observed_model_versions: list[str] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> tuple[float | None, str]:
    price_book = dict(BUILTIN_PRICE_BOOK_USD_PER_1M)
    pricing_source = "builtin"
    env_book = _load_env_price_book()
    if env_book:
        price_book.update(env_book)
        pricing_source = "mixed" if BUILTIN_PRICE_BOOK_USD_PER_1M else "env"

    for key in _candidate_price_keys(configured_model, *(observed_model_versions or [])):
        pricing = price_book.get(key)
        if not pricing:
            continue
        input_cost = (max(0, int(prompt_tokens)) / 1_000_000.0) * float(pricing["input"])
        output_cost = (max(0, int(completion_tokens)) / 1_000_000.0) * float(pricing["output"])
        return round(input_cost + output_cost, 8), pricing_source

    return None, "none"


def finalize_usage_collector(collector: dict[str, Any]) -> dict[str, Any] | None:
    if not collector:
        return None
    cost_usd, pricing_source = estimate_cost_usd(
        configured_model=str(collector.get("configured_model") or ""),
        observed_model_versions=list(collector.get("model_versions") or []),
        prompt_tokens=int(collector.get("prompt_tokens") or 0),
        completion_tokens=int(collector.get("completion_tokens") or 0),
    )
    row = {
        "role": collector.get("role"),
        "backend": collector.get("backend"),
        "configured_model": collector.get("configured_model"),
        "provider": collector.get("provider") or None,
        "model_versions": list(collector.get("model_versions") or []),
        "prompt_tokens": int(collector.get("prompt_tokens") or 0),
        "completion_tokens": int(collector.get("completion_tokens") or 0),
        "total_tokens": int(collector.get("total_tokens") or 0),
        "cached_prompt_tokens": int(collector.get("cached_prompt_tokens") or 0),
        "estimated_cost_usd": cost_usd,
        "pricing_source": pricing_source,
    }
    has_usage = any(
        int(row.get(key) or 0) > 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")
    )
    if has_usage or row["model_versions"]:
        return row
    return None


def aggregate_usage_events(
    events: list[dict[str, Any]],
    *,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    role_usage: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or str(event.get("type") or "") != "llm_usage":
            continue
        role = _normalize_role_name(str(event.get("role") or "unknown"))
        bucket = role_usage.setdefault(
            role,
            {
                "role": role,
                "backend": str(event.get("backend") or "").strip() or None,
                "configured_model": str(event.get("configured_model") or "").strip() or None,
                "provider": str(event.get("provider") or "").strip() or None,
                "model_versions": [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_prompt_tokens": 0,
                "estimated_cost_usd": 0.0,
                "pricing_sources": set(),
            },
        )
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens"):
            try:
                bucket[key] += int(event.get(key) or 0)
            except (TypeError, ValueError):
                continue
        raw_cost = event.get("estimated_cost_usd")
        if raw_cost not in (None, ""):
            try:
                bucket["estimated_cost_usd"] += float(raw_cost)
            except (TypeError, ValueError):
                pass
        pricing_source = str(event.get("pricing_source") or "").strip()
        if pricing_source:
            bucket["pricing_sources"].add(pricing_source)
        for model_version in event.get("model_versions") or []:
            text = str(model_version or "").strip()
            if text and text not in bucket["model_versions"]:
                bucket["model_versions"].append(text)

    usage_roles: list[dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cached_prompt_tokens = 0
    total_estimated_cost = 0.0
    pricing_sources: set[str] = set()
    for role_name in sorted(role_usage):
        bucket = role_usage[role_name]
        total_prompt_tokens += int(bucket["prompt_tokens"])
        total_completion_tokens += int(bucket["completion_tokens"])
        total_tokens += int(bucket["total_tokens"])
        total_cached_prompt_tokens += int(bucket["cached_prompt_tokens"])
        total_estimated_cost += float(bucket["estimated_cost_usd"] or 0.0)
        pricing_sources.update(bucket["pricing_sources"])
        usage_roles.append(
            {
                "role": bucket["role"],
                "backend": bucket["backend"],
                "configured_model": bucket["configured_model"],
                "provider": bucket["provider"],
                "model_versions": list(bucket["model_versions"]),
                "prompt_tokens": int(bucket["prompt_tokens"]),
                "completion_tokens": int(bucket["completion_tokens"]),
                "total_tokens": int(bucket["total_tokens"]),
                "cached_prompt_tokens": int(bucket["cached_prompt_tokens"]),
                "estimated_cost_usd": round(float(bucket["estimated_cost_usd"]), 8)
                if bucket["estimated_cost_usd"]
                else None,
                "pricing_source": (
                    "mixed"
                    if len(bucket["pricing_sources"]) > 1
                    else next(iter(bucket["pricing_sources"]), "none")
                ),
            }
        )

    return {
        "routing": get_model_routing_overview(roles=roles),
        "roles_used": usage_roles,
        "usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "cached_prompt_tokens": total_cached_prompt_tokens,
        },
        "estimated_cost_usd": round(total_estimated_cost, 8) if total_estimated_cost else None,
        "pricing_source": "mixed" if len(pricing_sources) > 1 else next(iter(pricing_sources), "none"),
    }

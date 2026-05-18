from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from dynamic_analyst.modeling import get_model_runtime_config

router = APIRouter()


def _normalize_path_template(path: str) -> str:
    return re.sub(r"\{[^/]+\}", "{}", (path or "").strip())


def _route_exists(request: Request, path: str, method: str) -> bool:
    target_method = (method or "GET").upper()
    target_path = _normalize_path_template(path)
    for route in request.app.routes:
        route_path = getattr(route, "path", None)
        route_methods = getattr(route, "methods", None) or set()
        if not route_path:
            continue
        if _normalize_path_template(route_path) != target_path:
            continue
        if target_method in route_methods:
            return True
    return False


def _evaluate_capability(request: Request, requirements: list[tuple[str, str]]) -> dict[str, Any]:
    missing = []
    for method, path in requirements:
        if not _route_exists(request, path, method):
            missing.append(f"{method.upper()} {path}")
    if missing:
        return {
            "available": False,
            "reason": f"Missing endpoint(s): {', '.join(missing)}",
            "missing_endpoints": missing,
        }
    return {"available": True}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


@router.get("/api/capabilities")
async def capabilities_endpoint(request: Request):
    feature_requirements: dict[str, list[tuple[str, str]]] = {
        "chat": [("POST", "/api/chat"), ("POST", "/api/chat/clear")],
        "ingestion": [("POST", "/api/upload"), ("POST", "/api/upload-url")],
        "crash_analysis": [("POST", "/api/crash/analyze")],
        "area_analysis": [("POST", "/api/area/analyze")],
        "workzone_analysis": [("POST", "/api/workzone/analyze")],
        "chart_editing": [
            ("POST", "/api/figure-edit"),
            ("POST", "/api/pipelines/hard_braking/apply"),
            ("POST", "/api/pipelines/workzone_analysis/apply"),
            ("POST", "/api/pipelines/crash_analysis/apply"),
        ],
        "roads_tiles": [("GET", "/tiles/roads/{z}/{x}/{y}.mvt"), ("GET", "/api/roads/bbox")],
    }

    features = {
        key: _evaluate_capability(request, requirements)
        for key, requirements in feature_requirements.items()
    }

    react_cfg = get_model_runtime_config("react")
    backend = str(react_cfg.get("backend") or "google")
    configured_model = str(react_cfg.get("configured_model") or "")
    api_key_source = "none"
    if backend == "google":
        api_key_source = "GOOGLE_API_KEY" if os.environ.get("GOOGLE_API_KEY", "").strip() else "none"
    elif backend == "litellm":
        api_key_source = "AGENT_LITELLM_API_KEY" if os.environ.get("AGENT_LITELLM_API_KEY", "").strip() else "none"
        if configured_model.startswith("openai/") or configured_model.startswith("openrouter/openai/"):
            api_key_source = f"{api_key_source} (OpenAI-compatible via LiteLLM)"

    return {
        "version": "capabilities-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "features": features,
        "agent": {
            "backend": backend,
            "model": configured_model,
            "api_key_configured": api_key_source != "none",
            "api_key_env": api_key_source,
            "note": (
                "OpenAI is used only when AGENT_MODEL_BACKEND=litellm and the model name "
                "starts with openai/ or openrouter/openai/. Default is Google Gemini via GOOGLE_API_KEY."
            ),
        },
        "limits": {
            "area_analysis_timeout_ms": _env_int("AREA_ANALYSIS_STMT_TIMEOUT_MS", 300_000),
            "workzone_analysis_timeout_ms": _env_int("WORKZONE_ANALYSIS_STMT_TIMEOUT_MS", 90_000),
            "area_analysis_fast_approx_area_km2": _env_int("AREA_ANALYSIS_FAST_APPROX_AREA_KM2", 600),
        },
    }

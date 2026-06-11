from __future__ import annotations

import json
from typing import Any, Optional

from dynamic_analyst.config import AGENT_SKILLS_ENABLED
from dynamic_analyst.orchestration.sql_dispatcher import run_unified_sql_query

from .builtin import build_builtin_plan
from .registry import get_skill_registry


def list_available_skills() -> list[dict[str, Any]]:
    registry = get_skill_registry()
    return registry.as_dicts()


def execute_skill(
    *,
    skill_id: str,
    args: Optional[dict[str, Any]] = None,
    user_query: str = "",
    force: bool = False,
) -> dict[str, Any]:
    if not AGENT_SKILLS_ENABLED and not force:
        return {
            "status": "disabled",
            "skill_id": skill_id,
            "fallback": "generic_react",
            "message": "Skills are disabled. Continue with generic tool loop.",
        }

    registry = get_skill_registry()
    spec = registry.get(skill_id)
    if spec is None:
        return {
            "status": "error",
            "skill_id": skill_id,
            "fallback": "generic_react",
            "message": f"Unknown skill_id '{skill_id}'.",
        }

    payload = args if isinstance(args, dict) else {}
    missing = [name for name in spec.required_inputs if not str(payload.get(name) or "").strip()]
    if missing:
        return {
            "status": "fallback",
            "skill_id": skill_id,
            "fallback": "generic_react",
            "message": f"Missing required skill inputs: {', '.join(missing)}",
        }

    try:
        plan = build_builtin_plan(skill_id, payload)
        raw = run_unified_sql_query(plan)
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {
            "status": "success",
            "skill_id": skill_id,
            "plan": plan,
            "data": parsed,
        }
    except Exception as exc:
        return {
            "status": "fallback",
            "skill_id": skill_id,
            "fallback": "generic_react",
            "message": str(exc),
        }

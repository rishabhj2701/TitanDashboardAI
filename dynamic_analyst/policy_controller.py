from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import AGENT_SKILLS_ENABLED
from .orchestration.sql_dispatcher import run_unified_sql_query
from .session_state import append_execution_event
from .skills.builtin import build_builtin_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyDecision:
    mode: str = "generic_react"
    intent_key: str = "general_query"
    skill_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    hard_rule: bool = False
    reason: str = ""
    candidate_skills: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _candidate_skill_entry(
    *,
    skill_id: str,
    args: dict[str, Any],
    reason: str,
    hard_rule: bool,
) -> dict[str, Any]:
    return {
        "skill_id": str(skill_id or "").strip(),
        "args": dict(args or {}),
        "reason": str(reason or "").strip(),
        "hard_rule": bool(hard_rule),
    }


def _extract_route_refs(text: str, max_items: int = 4) -> list[str]:
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b(i|us|mo|sr|route)\s*[- ]?\s*(\d{1,3})\b", raw, flags=re.IGNORECASE):
        prefix = m.group(1).upper()
        num = m.group(2)
        if prefix == "ROUTE":
            prefix = "MO"
        ref = f"{prefix}-{num}"
        if ref in seen:
            continue
        seen.add(ref)
        found.append(ref)
        if len(found) >= max(1, int(max_items)):
            break
    return found


def infer_intent_key(user_query: str) -> str:
    q = str(user_query or "").strip().lower()
    if not q:
        return "general_query"
    if any(token in q for token in ("hard braking", "hard brake", "hard_braking", "hardbraking")) and any(
        token in q for token in ("crash", "accident")
    ):
        return "crash_near_hard_brake"
    if any(token in q for token in ("compare", " vs ", " versus ", "graph", "plot")):
        return "grouped_comparison"
    if any(token in q for token in ("now", "same", "that", "those", "previous", "last")):
        return "followup_scope"
    if any(token in q for token in ("show", "display", "visualize", "map")) and _extract_route_refs(q, max_items=1):
        return "map_scope"
    if any(token in q for token in ("crash", "accident")):
        return "crash_query"
    if any(token in q for token in ("workzone", "work zone", "construction")):
        return "workzone_query"
    if any(token in q for token in ("road", "traffic", "speed", "vehicle")):
        return "traffic_query"
    return "general_query"


def _extract_speed_over_limit_threshold(text: str, default: float = 10.0) -> float:
    raw = str(text or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:mph\s*)?(?:over|above)\s*(?:the\s*)?speed\s*limit", raw)
    if not m:
        m = re.search(r"speed(?:ing)?\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:over|above)?", raw)
    if not m:
        return default
    try:
        value = float(m.group(1))
    except Exception:
        return default
    return max(1.0, min(40.0, value))


def _extract_density_threshold(text: str, default: int = 200) -> int:
    raw = str(text or "").lower()
    m = re.search(r"(\d{2,7})\s*\+?\s*(?:points?|count|density)", raw)
    if not m:
        return default
    try:
        value = int(m.group(1))
    except Exception:
        return default
    return max(1, min(1_000_000, value))


def _looks_like_single_corridor_map_request(q: str, route_refs: list[str]) -> bool:
    if len(route_refs) != 1:
        return False

    # "Incidents" can refer to traffic, crashes, or workzones. Require an
    # explicit traffic anchor before treating it as a traffic corridor request.
    if any(token in q for token in (" incident", " incidents", "incident ", "incidents ")):
        traffic_anchor_tokens = (
            "traffic",
            "road network",
            "speed",
            "congestion",
            "vehicle",
            "travel time",
        )
        if not any(token in q for token in traffic_anchor_tokens):
            return False

    # Keep ranking, comparison, and analytic prompts on generic ReAct.
    negative_tokens = (
        "compare",
        " vs ",
        " versus ",
        "speeding",
        "speed limit",
        "over limit",
        "density",
        "count",
        "counts",
        "highest",
        "top ",
        "most ",
        "rank",
        "ranking",
        "near crash",
        "near workzone",
        "hard braking",
        "hard-braking",
    )
    if any(token in q for token in negative_tokens):
        return False

    positive_tokens = (
        "show",
        "display",
        "visualize",
        "map",
        "traffic",
        "data",
        "corridor",
        "road network",
        "active",
        "just ",
        "only ",
    )
    return any(token in q for token in positive_tokens)


def _infer_domain_for_grouped_comparison(q: str) -> str:
    if any(token in q for token in ("crash", "accident")):
        return "crash"
    if any(token in q for token in ("workzone", "work zone", "construction")):
        return "workzone"
    if any(token in q for token in ("hard braking", "hard brake", "hard_braking", "hardbraking")):
        return "hard_braking"
    return "traffic"


def _infer_metric_for_grouped_comparison(q: str, domain: str) -> tuple[str, str]:
    if "average speed" in q or "avg speed" in q:
        return "speed", "avg"
    if any(token in q for token in ("graph average speed", "plot average speed", "chart average speed")):
        return "speed", "avg"
    if "speed" in q and any(token in q for token in ("average", "avg", "faster", "slower")):
        return "speed", "avg"
    if "speeding" in q or "over speed limit" in q or "over limit" in q:
        return "speed_over_limit", "avg"
    if "severity" in q:
        return "severity", "avg"
    if "count" in q or "counts" in q or "how many" in q:
        return "count", "count"
    return "count", "count"


def _looks_like_grouped_comparison_request(q: str, route_refs: list[str]) -> bool:
    compare_tokens = ("compare", " vs ", " versus ", "graph", "plot")
    if any(token in q for token in compare_tokens):
        return len(route_refs) >= 2 or " by " in q
    return False


def _match_candidate(user_query: str) -> PolicyDecision:
    q = str(user_query or "").strip().lower()
    intent_key = infer_intent_key(user_query)
    if not q:
        return PolicyDecision(intent_key=intent_key)

    route_refs = _extract_route_refs(q)
    route_ref = route_refs[0] if route_refs else ""

    # Enforce one action per turn for multi-corridor prompts.
    if len(route_refs) >= 2 and any(token in q for token in (" then ", " and then ", " after ", "switch to")):
        args = {"road_name_or_ref": route_ref, "map_limit": 2000}
        return PolicyDecision(
            mode="map_skill",
            intent_key=intent_key,
            skill_id="road_corridor_map_scope",
            args=args,
            hard_rule=True,
            reason="multi_action_route_first_only",
            candidate_skills=(
                _candidate_skill_entry(
                    skill_id="road_corridor_map_scope",
                    args=args,
                    reason="multi_action_route_first_only",
                    hard_rule=True,
                ),
            ),
        )

    if _looks_like_single_corridor_map_request(q, route_refs):
        args = {"road_name_or_ref": route_ref, "map_limit": 2000}
        return PolicyDecision(
            mode="map_skill",
            intent_key=intent_key,
            skill_id="road_corridor_map_scope",
            args=args,
            hard_rule=False,
            reason="single_corridor_map_scope",
            candidate_skills=(
                _candidate_skill_entry(
                    skill_id="road_corridor_map_scope",
                    args=args,
                    reason="single_corridor_map_scope",
                    hard_rule=False,
                ),
            ),
        )

    if _looks_like_grouped_comparison_request(q, route_refs):
        domain = _infer_domain_for_grouped_comparison(q)
        metric, aggregation = _infer_metric_for_grouped_comparison(q, domain)
        entities = route_refs[:4]
        view = "chart" if any(token in q for token in ("graph", "plot", "chart")) else "table"
        args = {
            "domain": domain,
            "group_by": ["road_name"] if entities else ["road_name"],
            "metric": metric,
            "aggregation": aggregation,
            "entities": entities,
            "view": view,
        }
        return PolicyDecision(
            intent_key=intent_key,
            reason="grouped_comparison_candidate",
            candidate_skills=(
                _candidate_skill_entry(
                    skill_id="grouped_comparison",
                    args=args,
                    reason="grouped_comparison_candidate",
                    hard_rule=False,
                ),
            ),
        )

    return PolicyDecision(intent_key=intent_key)


def decide_policy(user_query: str) -> PolicyDecision:
    base = _match_candidate(user_query)
    if base.skill_id and base.hard_rule:
        return base
    if not AGENT_SKILLS_ENABLED:
        return PolicyDecision(
            intent_key=infer_intent_key(user_query),
            candidate_skills=base.candidate_skills,
        )
    return base


def choose_direct_skill_route(user_query: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    # Route introspection should reflect intent matching regardless of runtime flags.
    decision = _match_candidate(user_query)
    if not decision.skill_id:
        return None, None
    return decision.skill_id, dict(decision.args)


def _text_from_skill_result(skill_id: str, data: Any, user_query: str) -> str:
    if isinstance(data, str) and data.strip():
        text = data.strip()
        if "REPORT FROM " in text and "FINAL DATA RESULTS:" in text:
            text = text.split("FINAL DATA RESULTS:", 1)[-1].strip()
        return text or f"Completed `{skill_id}` for: {user_query}"
    if isinstance(data, dict):
        for key in ("summary", "message", "response", "report"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(data, ensure_ascii=True, default=str)
    if isinstance(data, list):
        return json.dumps(data[:25], ensure_ascii=True, default=str)
    return f"Completed `{skill_id}` for: {user_query}"


def _fallback_text(skill_id: str, args: dict[str, Any], user_query: str) -> str | None:
    return None


async def execute_policy_decision(decision: PolicyDecision, user_query: str) -> str | None:
    if not decision.skill_id or decision.mode == "generic_react":
        append_execution_event(
            {
                "type": "policy_decision",
                "mode": "generic_react",
                "intent_key": decision.intent_key,
                "skill_id": "",
                "reason": decision.reason or "no_direct_policy",
            }
        )
        return None

    append_execution_event(
        {
            "type": "policy_decision",
            "mode": decision.mode,
            "intent_key": decision.intent_key,
            "skill_id": decision.skill_id,
            "args": dict(decision.args),
            "reason": decision.reason,
        }
    )
    try:
        plan = build_builtin_plan(decision.skill_id, dict(decision.args))
        append_execution_event(
            {
                "type": "tool_plan",
                "tool": "policy_route",
                "dataset": plan.get("domain"),
                "operations": [decision.skill_id],
                "user_query": user_query,
                "query_json": json.dumps(plan, ensure_ascii=True),
            }
        )
        raw = await asyncio.to_thread(run_unified_sql_query, plan)
        parsed: Any = raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
        text = _text_from_skill_result(decision.skill_id, parsed, user_query).strip()
        append_execution_event(
            {
                "type": "policy_result",
                "skill_id": decision.skill_id,
                "mode": decision.mode,
                "used": True,
                "success": True,
                "returned_text": bool(text),
            }
        )
        return text if text else None
    except Exception as exc:
        logger.warning("policy skill execution failed mode=%s skill=%s error=%s", decision.mode, decision.skill_id, str(exc), exc_info=True)
        append_execution_event(
            {
                "type": "policy_result",
                "skill_id": decision.skill_id,
                "mode": decision.mode,
                "used": True,
                "success": False,
                "error": str(exc),
                "fallback": "generic_react",
            }
        )
        return None


async def run_direct_policy_route(user_query: str) -> str | None:
    decision = decide_policy(user_query)
    if not decision.hard_rule:
        append_execution_event(
            {
                "type": "policy_decision",
                "mode": "generic_react",
                "intent_key": decision.intent_key,
                "skill_id": "",
                "reason": decision.reason or "soft_policy_suggestion_only",
                "candidate_skills": [dict(item) for item in decision.candidate_skills],
            }
        )
        return None
    return await execute_policy_decision(decision, user_query)

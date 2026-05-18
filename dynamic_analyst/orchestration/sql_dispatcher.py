"""Unified SQL domain dispatcher.

This is the orchestration seam for planner-driven SQL execution.
"""

from __future__ import annotations

from typing import Any, Callable

from .contracts import SqlPlan
from dynamic_analyst.config import (
    SQL_CONFLATION_MIN_DISTANCE_M,
    SQL_CONFLATION_MAX_DISTANCE_M,
    SQL_CONFLATION_MIN_WINDOW_MIN,
    SQL_CONFLATION_MAX_WINDOW_MIN,
)

_CONFLATION_OPERATION = "conflation_crashes_near_hard_braking"
_MIN_DISTANCE_M = SQL_CONFLATION_MIN_DISTANCE_M
_MAX_DISTANCE_M = SQL_CONFLATION_MAX_DISTANCE_M
_MIN_WINDOW_MIN = SQL_CONFLATION_MIN_WINDOW_MIN
_MAX_WINDOW_MIN = SQL_CONFLATION_MAX_WINDOW_MIN
_MIN_LIMIT = 1
_MAX_LIMIT = 10000

_DOMAIN_ALIASES: dict[str, str] = {
    "traffic": "traffic",
    "cv": "traffic",
    "connected_vehicle": "traffic",
    "connectedvehicle": "traffic",
    "hard_braking": "hard_braking",
    "hard-braking": "hard_braking",
    "hardbraking": "hard_braking",
    "hard_brake": "hard_braking",
    "hard-brake": "hard_braking",
    "hardbrake": "hard_braking",
    "hb": "hard_braking",
    "crash": "crash",
    "crashes": "crash",
    "accident": "crash",
    "accidents": "crash",
    "event": "crash",
    "events": "crash",
    "workzone": "workzone",
    "workzones": "workzone",
    "construction": "workzone",
    "wzdx": "workzone",
    "conflation": "conflation",
    "cross_domain": "conflation",
    "crossdomain": "conflation",
    "multi_domain": "conflation",
    "spatial_join": "conflation",
}


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _first_operation_step(query: dict[str, Any], operation: str) -> dict[str, Any] | None:
    target = str(operation or "").strip().lower()
    steps = query.get("steps")
    if not target or not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("operation") or "").strip().lower()
        if op != target:
            continue
        params = step.get("params") or {}
        return params if isinstance(params, dict) else {}
    return None


def _run_crash_hard_brake_conflation(params: dict[str, Any]) -> str:
    from ..sql.conflation_ops import run_generic_conflation
    from ..services.conflation_service import is_hard_braking_ref

    distance_m = _bounded_float(
        params.get("distance_m"),
        default=200.0,
        minimum=_MIN_DISTANCE_M,
        maximum=_MAX_DISTANCE_M,
    )
    window_min = _bounded_int(
        params.get("time_window_minutes"),
        default=60,
        minimum=_MIN_WINDOW_MIN,
        maximum=_MAX_WINDOW_MIN,
    )
    limit = _bounded_int(
        params.get("limit"),
        default=5000,
        minimum=_MIN_LIMIT,
        maximum=_MAX_LIMIT,
    )
    time_mode = str(params.get("time_mode") or "window").strip().lower()
    if time_mode not in {"window", "none", "auto"}:
        time_mode = "window"
    generate_map = bool(params.get("generate_map", True))

    crash_dataset = str(params.get("crash_dataset_id") or "crashes")
    hard_brake_dataset_raw = str(params.get("hard_brake_dataset_id") or "").strip()
    # This operation is semantically fixed to the hard-braking source.
    # Planner may emit active CV run ids here; normalize those to the hard-braking alias.
    hard_brake_dataset = (
        hard_brake_dataset_raw
        if hard_brake_dataset_raw and is_hard_braking_ref(hard_brake_dataset_raw)
        else "hard_braking"
    )

    return run_generic_conflation(
        left_dataset=crash_dataset,
        right_dataset=hard_brake_dataset,
        max_distance_meters=distance_m,
        time_mode=time_mode,
        time_window_minutes=window_min,
        generate_map=generate_map,
        limit=limit,
    )


def normalize_sql_domain(domain: str) -> str:
    value = str(domain or "").strip().lower()
    if not value:
        raise ValueError("SQL plan missing required 'domain'.")
    normalized = _DOMAIN_ALIASES.get(value)
    if not normalized:
        supported = ", ".join(sorted(set(_DOMAIN_ALIASES.values())))
        raise ValueError(f"Unsupported SQL domain '{domain}'. Supported: {supported}.")
    return normalized


def supported_sql_domains() -> list[str]:
    return sorted(set(_DOMAIN_ALIASES.values()))


def _run_conflation_from_plan(plan: dict[str, Any]) -> str:
    """Adapt a planner-emitted conflation plan into a call to the existing
    conflation_ops functions."""
    from ..sql.conflation_ops import run_generic_conflation, run_multi_dataset_conflation

    datasets = plan.get("datasets") or []
    if not datasets or len(datasets) < 2:
        raise ValueError(
            "Conflation plan requires 'datasets' with at least 2 entries "
            '(e.g., ["crashes", "workzones"]).'
        )

    max_distance = float(plan.get("max_distance_meters", 500))
    time_mode = str(plan.get("time_mode", "auto"))
    time_window = int(plan.get("time_window_minutes", 60))
    generate_map = bool(plan.get("generate_map", True))
    limit = int(plan.get("limit", 5000))

    if len(datasets) == 2:
        return run_generic_conflation(
            left_dataset=datasets[0],
            right_dataset=datasets[1],
            max_distance_meters=max_distance,
            time_mode=time_mode,
            time_window_minutes=time_window,
            generate_map=generate_map,
            limit=limit,
        )
    return run_multi_dataset_conflation(
        dataset_names=datasets,
        max_distance_meters=max_distance,
        time_mode=time_mode,
        time_window_minutes=time_window,
        limit=limit,
    )


def _domain_runners() -> dict[str, Callable[[dict[str, Any]], str]]:
    # Imported lazily to avoid heavy import-time dependencies and circulars.
    from ..sql.crash import run_crash_sql_operations
    from ..sql.traffic import run_traffic_sql_operations
    from ..sql.workzone import run_workzone_sql_operations

    return {
        "traffic": lambda query: run_traffic_sql_operations("traffic", query),
        "hard_braking": lambda query: run_traffic_sql_operations(
            "hard_braking",
            {**query, "hard_brake_only": True},
        ),
        "crash": lambda query: run_crash_sql_operations("crash", query),
        "workzone": lambda query: run_workzone_sql_operations("workzone", query),
        "conflation": _run_conflation_from_plan,
    }


def run_sql_domain_operations(domain: str, query: dict[str, Any]) -> str:
    canonical = normalize_sql_domain(domain)
    if not isinstance(query, dict):
        raise ValueError("SQL query payload must be a JSON object.")

    conflation_step = _first_operation_step(query, _CONFLATION_OPERATION)
    if conflation_step is not None:
        if canonical != "hard_braking":
            raise ValueError(
                f"{_CONFLATION_OPERATION} is only supported with domain='hard_braking'."
            )
        return _run_crash_hard_brake_conflation(conflation_step)

    runner = _domain_runners().get(canonical)
    if not runner:
        supported = ", ".join(supported_sql_domains())
        raise ValueError(f"No SQL runner configured for '{canonical}'. Supported: {supported}.")
    return runner(query)


def run_unified_sql_query(plan: SqlPlan | dict[str, Any]) -> str:
    """Run a planner-emitted SQL plan.

    Accepted forms:
    1) {"domain": "...", "reasoning": "...", "steps": [...], ...}
    2) {"domain": "...", "query": {"reasoning": "...", "steps": [...], ...}}
    """
    if not isinstance(plan, dict):
        raise ValueError("SQL plan must be a JSON object.")

    domain = plan.get("domain")
    if not domain:
        raise ValueError("SQL plan missing required 'domain'.")

    payload = plan.get("query")
    if payload is None:
        payload = dict(plan)
        payload.pop("domain", None)
    if not isinstance(payload, dict):
        raise ValueError("SQL plan field 'query' must be a JSON object when provided.")

    return run_sql_domain_operations(str(domain), payload)

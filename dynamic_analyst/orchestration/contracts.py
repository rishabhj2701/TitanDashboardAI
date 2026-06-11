"""Shared contracts for planner to SQL execution."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class SqlPlan(TypedDict):
    """Normalized planner output for SQL execution."""

    domain: str
    reasoning: NotRequired[str]
    dataset_id: NotRequired[str]
    steps: NotRequired[list[dict[str, Any]]]
    # Conflation-specific fields (used when domain="conflation")
    datasets: NotRequired[list[str]]
    max_distance_meters: NotRequired[float]
    time_mode: NotRequired[str]
    time_window_minutes: NotRequired[int]
    generate_map: NotRequired[bool]
    limit: NotRequired[int]

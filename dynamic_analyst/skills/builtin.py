from __future__ import annotations

import re
from typing import Any

from .types import SkillSpec


ROAD_CORRIDOR_MAP_SCOPE = SkillSpec(
    skill_id="road_corridor_map_scope",
    version="1.0.0",
    description="Scope traffic map to a requested road/corridor with a map-first result.",
    intent_tags=["map_scope", "followup_scope"],
    domain="traffic",
    required_inputs=["road_name_or_ref"],
    input_schema={
        "type": "object",
        "properties": {
            "road_name_or_ref": {"type": "string"},
            "dataset_id": {"type": "string"},
            "map_limit": {"type": "integer", "minimum": 100, "maximum": 10000},
        },
        "required": ["road_name_or_ref"],
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


CRASH_NEAR_HARD_BRAKE_CONFLATION = SkillSpec(
    skill_id="crash_near_hard_brake_conflation",
    version="1.0.0",
    description="Find crashes spatially and temporally near hard-braking events.",
    intent_tags=["crash_near_hard_brake", "conflation"],
    domain="hard_braking",
    required_inputs=[],
    input_schema={
        "type": "object",
        "properties": {
            "distance_m": {"type": "number", "minimum": 1, "maximum": 2000},
            "time_window_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
        },
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


CRASH_MAP_OVERVIEW = SkillSpec(
    skill_id="crash_map_overview",
    version="1.0.0",
    description="Generate a crash map overview without dumping all raw rows.",
    intent_tags=["map_scope", "crash_overview"],
    domain="crash",
    required_inputs=[],
    input_schema={
        "type": "object",
        "properties": {
            "dataset_id": {"type": "string"},
            "map_limit": {"type": "integer", "minimum": 500, "maximum": 20000},
        },
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


ROADS_SPEEDING_OVER_LIMIT = SkillSpec(
    skill_id="roads_speeding_over_limit",
    version="1.0.0",
    description="Rank roads where average speed is above the speed limit by a threshold.",
    intent_tags=["speeding_roads", "ranking_query"],
    domain="traffic",
    required_inputs=[],
    input_schema={
        "type": "object",
        "properties": {
            "min_avg_speed_over_limit_mph": {"type": "number", "minimum": 1, "maximum": 40},
            "min_point_count": {"type": "integer", "minimum": 1, "maximum": 500000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "dataset_id": {"type": "string"},
            "map_limit": {"type": "integer", "minimum": 500, "maximum": 20000},
        },
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


ROADS_DENSITY_FILTER = SkillSpec(
    skill_id="roads_density_filter",
    version="1.0.0",
    description="Filter and rank roads by event density proxy (point count) from stats.",
    intent_tags=["density_roads", "ranking_query"],
    domain="traffic",
    required_inputs=[],
    input_schema={
        "type": "object",
        "properties": {
            "min_density_count": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "dataset_id": {"type": "string"},
            "map_limit": {"type": "integer", "minimum": 500, "maximum": 20000},
        },
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


GROUPED_COMPARISON = SkillSpec(
    skill_id="grouped_comparison",
    version="1.0.0",
    description="Compare an aggregated metric across a bounded set of groups or entities.",
    intent_tags=["comparison_query", "ranking_query"],
    domain="generic",
    required_inputs=["domain", "metric", "aggregation"],
    input_schema={
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "group_by": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
                ]
            },
            "metric": {"type": "string"},
            "aggregation": {"type": "string"},
            "entities": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
            "filters": {"type": "array", "items": {"type": "object"}},
            "dataset_id": {"type": "string"},
            "view": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "sort_order": {"type": "string"},
        },
        "required": ["domain", "metric", "aggregation"],
    },
    safety_checks=[
        "schema_validate_before_execute",
        "queryable_policy_enforced",
    ],
)


def list_builtin_specs() -> list[SkillSpec]:
    return [
        ROAD_CORRIDOR_MAP_SCOPE,
        CRASH_NEAR_HARD_BRAKE_CONFLATION,
        CRASH_MAP_OVERVIEW,
        ROADS_SPEEDING_OVER_LIMIT,
        ROADS_DENSITY_FILTER,
        GROUPED_COMPARISON,
    ]


def _normalize_group_by(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    return []


def _safe_alias(text: str, default: str = "comparison_value") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or default


def _comparison_filter_conditions(group_col: str, entities: list[str]) -> list[dict[str, Any]]:
    normalized_col = str(group_col or "").strip()
    if not normalized_col:
        return []
    if normalized_col in {"road", "road_name", "name"}:
        return [{"column": normalized_col, "operator": "contains", "value": entity} for entity in entities]
    return [{"column": normalized_col, "operator": "in", "value": list(entities)}]


def _normalize_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "crashes": "crash",
        "accidents": "crash",
        "events": "crash",
        "wzdx": "workzone",
        "workzones": "workzone",
        "construction": "workzone",
        "hard braking": "hard_braking",
        "hardbraking": "hard_braking",
    }
    return aliases.get(text, text or "traffic")


def _infer_default_group_by(domain: str, entities: list[str]) -> list[str]:
    if entities:
        return ["road_name"]
    if domain == "crash":
        return ["road_name"]
    if domain == "workzone":
        return ["road_name"]
    return ["road_name"]


def _normalize_comparison_metric(domain: str, metric: Any, aggregation: Any) -> tuple[str, str, str]:
    metric_text = str(metric or "").strip().lower().replace(" ", "_")
    aggregation_text = str(aggregation or "").strip().lower()
    if not aggregation_text:
        aggregation_text = "count"

    if metric_text in {"count", "counts", "record_count", "crash_count", "workzone_count"}:
        return "count", "count", "count"

    if metric_text in {"avg_speed", "average_speed", "speed", "speed_mph"}:
        return "speed", "avg", "avg_speed"

    if metric_text in {"speed_over_limit", "avg_speed_over_limit", "speeding_rate", "speeding"}:
        return "speed_over_limit", "avg", "avg_speed_over_limit"

    if metric_text in {"severity", "avg_severity", "average_severity"}:
        return "severity", "avg", "avg_severity"

    if aggregation_text in {"avg", "mean", "average"}:
        aggregation_text = "avg"
    elif aggregation_text in {"count", "total"}:
        aggregation_text = "count"

    alias = _safe_alias(f"{aggregation_text}_{metric_text}", default="comparison_value")
    return metric_text or "count", aggregation_text or "count", alias


def build_builtin_plan(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    sid = str(skill_id or "").strip()
    payload = args if isinstance(args, dict) else {}

    if sid == "road_corridor_map_scope":
        road_value = str(payload.get("road_name_or_ref") or "").strip()
        if not road_value:
            raise ValueError("road_corridor_map_scope requires road_name_or_ref")
        map_limit = int(payload.get("map_limit") or 2000)
        map_limit = max(100, min(10000, map_limit))
        plan: dict[str, Any] = {
            "domain": "traffic",
            "reasoning": "Built-in skill: road corridor scoped map",
            "steps": [
                {
                    "operation": "filter",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "road_name", "operator": "ilike", "value": f"%{road_value}%"},
                        ],
                    },
                },
                {
                    "operation": "generate_map",
                    "params": {
                        "map_type": "points",
                        "label_column": "road_name",
                        "limit": map_limit,
                    },
                },
            ],
        }
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if dataset_id:
            plan["dataset_id"] = dataset_id
        return plan

    if sid == "crash_near_hard_brake_conflation":
        distance_m = float(payload.get("distance_m") or 200.0)
        distance_m = max(1.0, min(2000.0, distance_m))
        window_min = int(payload.get("time_window_minutes") or 60)
        window_min = max(0, min(24 * 60, window_min))
        limit = int(payload.get("limit") or 5000)
        limit = max(1, min(10000, limit))
        return {
            "domain": "hard_braking",
            "reasoning": "Built-in skill: crashes near hard braking conflation",
            "steps": [
                {
                    "operation": "conflation_crashes_near_hard_braking",
                    "params": {
                        "distance_m": distance_m,
                        "time_window_minutes": window_min,
                        "limit": limit,
                        "generate_map": True,
                    },
                }
            ],
        }

    if sid == "crash_map_overview":
        map_limit = int(payload.get("map_limit") or 8000)
        map_limit = max(500, min(20000, map_limit))
        plan = {
            "domain": "crash",
            "reasoning": "Built-in skill: crash map overview",
            "steps": [
                {
                    "operation": "generate_map",
                    "params": {
                        "map_type": "points",
                        "label_column": "road_name",
                        "limit": map_limit,
                    },
                }
            ],
        }
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if dataset_id:
            plan["dataset_id"] = dataset_id
        return plan

    if sid == "roads_speeding_over_limit":
        threshold = float(payload.get("min_avg_speed_over_limit_mph") or 10.0)
        threshold = max(1.0, min(40.0, threshold))
        min_points = int(payload.get("min_point_count") or 100)
        min_points = max(1, min(500000, min_points))
        limit = int(payload.get("limit") or 25)
        limit = max(1, min(200, limit))
        map_limit = int(payload.get("map_limit") or 5000)
        map_limit = max(500, min(20000, map_limit))
        plan = {
            "domain": "traffic",
            "reasoning": "Built-in skill: roads with average speeding over limit threshold",
            "steps": [
                {
                    "operation": "groupby",
                    "params": {
                        "group_by": ["road_name"],
                        "aggregations": {
                            "avg_speed_over_limit_mph": {"fn": "mean", "column": "speed_over_limit"},
                            "point_count": {"fn": "count", "column": "count"},
                        },
                    },
                },
                {
                    "operation": "having",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "avg_speed_over_limit_mph", "operator": ">=", "value": threshold},
                            {"column": "point_count", "operator": ">=", "value": min_points},
                        ],
                    },
                },
                {
                    "operation": "sort",
                    "params": {"sort_by": "avg_speed_over_limit_mph", "order": "desc"},
                },
                {
                    "operation": "head",
                    "params": {"n": limit},
                },
            ],
        }
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if dataset_id:
            plan["dataset_id"] = dataset_id
        return plan

    if sid == "roads_density_filter":
        min_density = int(payload.get("min_density_count") or 200)
        min_density = max(1, min(1000000, min_density))
        limit = int(payload.get("limit") or 25)
        limit = max(1, min(200, limit))
        map_limit = int(payload.get("map_limit") or 5000)
        map_limit = max(500, min(20000, map_limit))
        plan = {
            "domain": "traffic",
            "reasoning": "Built-in skill: roads filtered by density proxy from stats",
            "steps": [
                {
                    "operation": "groupby",
                    "params": {
                        "group_by": ["road_name"],
                        "aggregations": {
                            "density_count": {"fn": "count", "column": "count"},
                            "avg_speed_mph": {"fn": "mean", "column": "speed"},
                        },
                    },
                },
                {
                    "operation": "having",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "density_count", "operator": ">=", "value": min_density},
                        ],
                    },
                },
                {
                    "operation": "sort",
                    "params": {"sort_by": "density_count", "order": "desc"},
                },
                {
                    "operation": "head",
                    "params": {"n": limit},
                },
            ],
        }
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if dataset_id:
            plan["dataset_id"] = dataset_id
        return plan

    if sid == "grouped_comparison":
        domain = _normalize_domain(payload.get("domain"))
        if not domain:
            raise ValueError("grouped_comparison requires domain")
        metric_input = payload.get("metric")
        if not str(metric_input or "").strip():
            raise ValueError("grouped_comparison requires metric")
        aggregation_input = payload.get("aggregation")
        if not str(aggregation_input or "").strip():
            raise ValueError("grouped_comparison requires aggregation")
        entities = [str(item or "").strip() for item in (payload.get("entities") or []) if str(item or "").strip()]
        group_by = _normalize_group_by(payload.get("group_by") or _infer_default_group_by(domain, entities))
        if not group_by:
            raise ValueError("grouped_comparison requires a non-empty group_by")
        filters = payload.get("filters") or []
        if filters and not isinstance(filters, list):
            raise ValueError("grouped_comparison filters must be a list")
        metric, aggregation, metric_alias = _normalize_comparison_metric(domain, metric_input, aggregation_input)
        alias_base = payload.get("alias")
        if alias_base:
            metric_alias = _safe_alias(str(alias_base), default=metric_alias)
        sort_order = str(payload.get("sort_order") or "desc").strip().lower()
        sort_order = "asc" if sort_order == "asc" else "desc"
        limit = int(payload.get("limit") or max(10, len(entities) or 10))
        limit = max(1, min(200, limit))
        view = str(payload.get("view") or "").strip().lower()

        if aggregation == "count":
            aggregation_expr: dict[str, Any] = {"fn": "count", "column": metric}
        else:
            aggregation_expr = {"fn": aggregation, "column": metric}

        steps: list[dict[str, Any]] = []
        if entities:
            steps.append(
                {
                    "operation": "filter",
                    "params": {
                        "mode": "or",
                        "conditions": _comparison_filter_conditions(group_by[0], entities),
                    },
                }
            )
        if filters:
            steps.append(
                {
                    "operation": "filter",
                    "params": {
                        "mode": "and",
                        "conditions": list(filters),
                    },
                }
            )
        steps += [
            {
                "operation": "groupby",
                "params": {
                    "group_by": group_by,
                    "aggregations": {
                        metric_alias: aggregation_expr,
                    },
                },
            },
            {
                "operation": "sort",
                "params": {"sort_by": metric_alias, "order": sort_order},
            },
            {
                "operation": "head",
                "params": {"n": limit},
            },
        ]
        reasoning = "Built-in skill: grouped comparison"
        if entities:
            reasoning = f"{reasoning} across {', '.join(entities[:4])}"
        if view:
            reasoning = f"{reasoning} ({view})"
        plan = {
            "domain": domain,
            "reasoning": reasoning,
            "steps": steps,
        }
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if dataset_id:
            plan["dataset_id"] = dataset_id
        return plan

    raise ValueError(f"Unknown skill_id '{skill_id}'")

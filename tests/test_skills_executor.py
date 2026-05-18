import json

from dynamic_analyst.skills import executor
from dynamic_analyst.skills.builtin import build_builtin_plan


def test_execute_skill_returns_disabled_when_feature_off():
    result = executor.execute_skill(skill_id="road_corridor_map_scope", args={"road_name_or_ref": "I-70"})
    assert result["status"] in {"disabled", "fallback"}


def test_execute_skill_success_path(monkeypatch):
    monkeypatch.setattr(executor, "AGENT_SKILLS_ENABLED", True)
    monkeypatch.setattr(executor, "run_unified_sql_query", lambda plan: json.dumps({"status": "ok", "plan": plan}))

    result = executor.execute_skill(
        skill_id="road_corridor_map_scope",
        args={"road_name_or_ref": "I-70"},
        user_query="show i-70 traffic",
    )
    assert result["status"] == "success"
    assert result["skill_id"] == "road_corridor_map_scope"


def test_build_builtin_plan_grouped_comparison_for_route_compare():
    plan = build_builtin_plan(
        "grouped_comparison",
        {
            "domain": "traffic",
            "group_by": ["road_name"],
            "metric": "speed",
            "aggregation": "avg",
            "entities": ["I-70", "I-44"],
            "view": "chart",
        },
    )
    assert plan["domain"] == "traffic"
    assert plan["steps"][0]["operation"] == "filter"
    assert plan["steps"][0]["params"]["mode"] == "or"
    assert plan["steps"][1]["operation"] == "groupby"
    assert plan["steps"][1]["params"]["aggregations"]["avg_speed"]["column"] == "speed"
    assert "(chart)" in plan["reasoning"]


def test_build_builtin_plan_grouped_comparison_for_crash_count_compare():
    plan = build_builtin_plan(
        "grouped_comparison",
        {
            "domain": "crash",
            "group_by": ["road_name"],
            "metric": "count",
            "aggregation": "count",
            "entities": ["I-70", "I-44"],
        },
    )
    assert plan["domain"] == "crash"
    assert plan["steps"][1]["params"]["aggregations"]["count"]["fn"] == "count"
    assert plan["steps"][1]["params"]["aggregations"]["count"]["column"] == "count"


def test_validate_agent_config_is_noop_for_deterministic_skills(monkeypatch):
    import dynamic_analyst.config as cfg
    monkeypatch.setattr(cfg, "AGENT_SKILLS_ENABLED", True)
    cfg.validate_agent_config()


def test_execute_skill_exception_returns_fallback_with_message(monkeypatch):
    """RELY-01: execute_skill returns a fallback dict with error message when tool call raises."""
    monkeypatch.setattr(executor, "AGENT_SKILLS_ENABLED", True)

    # Inject a RuntimeError into run_unified_sql_query (the inner tool-call path).
    monkeypatch.setattr(executor, "run_unified_sql_query", lambda plan: (_ for _ in ()).throw(RuntimeError("skill boom")))

    result = executor.execute_skill(
        skill_id="road_corridor_map_scope",
        args={"road_name_or_ref": "I-70"},
        user_query="show me traffic on I-70",
    )
    assert result is not None
    assert isinstance(result, dict)
    assert result.get("status") is not None
    assert "skill boom" in str(result)

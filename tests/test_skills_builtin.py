from dynamic_analyst.skills.builtin import build_builtin_plan


def test_build_crash_map_overview_skill_plan_is_map_first():
    plan = build_builtin_plan("crash_map_overview", {"map_limit": 9000})
    assert plan["domain"] == "crash"
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["operation"] == "generate_map"
    assert int(plan["steps"][0]["params"]["limit"]) == 9000


def test_build_roads_speeding_over_limit_plan_has_groupby_and_having():
    plan = build_builtin_plan(
        "roads_speeding_over_limit",
        {
            "min_avg_speed_over_limit_mph": 10,
            "min_point_count": 120,
            "limit": 15,
        },
    )
    ops = [step.get("operation") for step in plan.get("steps", [])]
    assert plan["domain"] == "traffic"
    assert "groupby" in ops
    assert "having" in ops
    assert "generate_map" not in ops


def test_build_roads_density_filter_plan_has_density_having():
    plan = build_builtin_plan("roads_density_filter", {"min_density_count": 400, "limit": 10})
    assert plan["domain"] == "traffic"
    having = next(step for step in plan["steps"] if step["operation"] == "having")
    conditions = having["params"].get("conditions", [])
    assert any(str(cond.get("column")) == "density_count" for cond in conditions)

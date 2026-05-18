import asyncio

import dynamic_analyst.policy_controller as policy_controller


def test_decide_policy_multi_route_uses_first_route(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("show I-70 traffic then switch to I-44")
    assert decision.skill_id == "road_corridor_map_scope"
    assert decision.args.get("road_name_or_ref") == "I-70"


def test_decide_policy_single_corridor_map_request_uses_scope_skill(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Show just I-70 traffic data")
    assert decision.skill_id == "road_corridor_map_scope"
    assert decision.args.get("road_name_or_ref") == "I-70"
    assert decision.hard_rule is False
    assert decision.candidate_skills[0]["skill_id"] == "road_corridor_map_scope"


def test_decide_policy_density_request_uses_generic_react(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("show roads filtered by density")
    assert decision.mode == "generic_react"
    assert decision.skill_id == ""


def test_run_direct_policy_route_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)

    def _raise(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(policy_controller, "run_unified_sql_query", _raise)
    text = asyncio.run(policy_controller.run_direct_policy_route("show I-70 traffic then switch to I-44"))
    assert text is None


def test_decide_policy_returns_generic_for_non_analytic(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("hello there")
    assert decision.mode == "generic_react"
    assert decision.skill_id == ""


def test_decide_policy_corridor_switch_uses_generic_react(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("now switch to I-44")
    assert decision.mode == "generic_react"
    assert decision.skill_id == ""


def test_decide_policy_single_corridor_compare_request_uses_generic_react(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("compare I-70 traffic vs I-44")
    assert decision.mode == "generic_react"
    assert decision.skill_id == ""
    assert decision.candidate_skills[0]["skill_id"] == "grouped_comparison"
    assert decision.candidate_skills[0]["args"]["domain"] == "traffic"
    assert decision.candidate_skills[0]["args"]["entities"] == ["I-70", "I-44"]


def test_decide_policy_crash_compare_sets_grouped_comparison_candidate(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Compare crash counts on I-70 vs I-44")
    assert decision.skill_id == ""
    assert decision.candidate_skills[0]["skill_id"] == "grouped_comparison"
    assert decision.candidate_skills[0]["args"]["domain"] == "crash"
    assert decision.candidate_skills[0]["args"]["metric"] == "count"
    assert decision.candidate_skills[0]["args"]["aggregation"] == "count"


def test_decide_policy_speed_compare_sets_avg_speed_grouped_comparison(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Graph average speed on I-70 vs I-44")
    assert decision.skill_id == ""
    assert decision.candidate_skills[0]["skill_id"] == "grouped_comparison"
    assert decision.candidate_skills[0]["args"]["domain"] == "traffic"
    assert decision.candidate_skills[0]["args"]["metric"] == "speed"
    assert decision.candidate_skills[0]["args"]["aggregation"] == "avg"
    assert decision.candidate_skills[0]["args"]["view"] == "chart"


def test_decide_policy_incidents_request_uses_generic_react(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Show incidents on I-70")
    assert decision.mode == "generic_react"
    assert decision.skill_id == ""


def test_decide_policy_traffic_incidents_request_keeps_scope_skill(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Show traffic incidents on I-70")
    assert decision.skill_id == "road_corridor_map_scope"
    assert decision.args.get("road_name_or_ref") == "I-70"


def test_decide_policy_multi_route_still_routes_when_skills_flag_off(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", False)
    decision = policy_controller.decide_policy("show I-70 traffic then switch to I-44")
    assert decision.skill_id == "road_corridor_map_scope"
    assert decision.hard_rule is True


def test_run_direct_policy_route_skips_soft_suggestions(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    text = asyncio.run(policy_controller.run_direct_policy_route("Show just I-70 traffic data"))
    assert text is None


def test_execute_policy_decision_logs_exception(monkeypatch, caplog):
    """RELY-01: execute_policy_decision logs full exception context on failure."""
    import logging

    # Monkeypatch the SQL execution path in policy_controller to raise
    def _raise(_plan):
        raise RuntimeError("skill execution boom")

    monkeypatch.setattr(policy_controller, "run_unified_sql_query", _raise)
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)

    # Build a hard-rule decision so execute_policy_decision actually runs the skill
    decision = policy_controller.PolicyDecision(
        mode="map_skill",
        intent_key="test_intent",
        skill_id="road_corridor_map_scope",
        args={"road_name_or_ref": "I-70", "map_limit": 2000},
        hard_rule=True,
        reason="test",
    )

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(policy_controller.execute_policy_decision(decision, "show I-70 map"))

    # Silent fallback: returns None (per CONTEXT.md decision)
    assert result is None
    # But the exception IS logged
    assert "skill execution boom" in caplog.text

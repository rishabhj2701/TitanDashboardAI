import asyncio
import json

import dynamic_analyst.policy_controller as policy_controller
import dynamic_analyst.pipeline_react as pipeline_react
from dynamic_analyst import session_state


def test_choose_direct_skill_route_for_speeding_returns_none():
    skill_id, args = pipeline_react._choose_direct_skill_route("show roads with speeding 10+ over speed limit")
    assert skill_id is None
    assert args is None


def test_choose_direct_skill_route_for_density_returns_none():
    skill_id, args = pipeline_react._choose_direct_skill_route("show roads filtered by density")
    assert skill_id is None
    assert args is None


def test_choose_direct_skill_route_for_corridor_switch_returns_none():
    skill_id, args = pipeline_react._choose_direct_skill_route("now switch to I-44")
    assert skill_id is None
    assert args is None


def test_choose_direct_skill_route_single_corridor_map_request_uses_scope_skill():
    skill_id, args = pipeline_react._choose_direct_skill_route("Show just I-70 traffic data")
    assert skill_id == "road_corridor_map_scope"
    assert args.get("road_name_or_ref") == "I-70"


def test_choose_direct_skill_route_multi_action_uses_first_route():
    skill_id, args = pipeline_react._choose_direct_skill_route("show I-70 traffic then switch to I-44")
    assert skill_id == "road_corridor_map_scope"
    assert args.get("road_name_or_ref") == "I-70"


def test_choose_direct_skill_route_incidents_request_returns_none():
    skill_id, args = pipeline_react._choose_direct_skill_route("Show incidents on I-70")
    assert skill_id is None
    assert args is None


def test_choose_direct_skill_route_traffic_incidents_uses_scope_skill():
    skill_id, args = pipeline_react._choose_direct_skill_route("Show traffic incidents on I-70")
    assert skill_id == "road_corridor_map_scope"
    assert args.get("road_name_or_ref") == "I-70"


def test_run_direct_skill_route_returns_text_for_multi_route_guardrail(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    monkeypatch.setattr(policy_controller, "run_unified_sql_query", lambda plan: "FINAL DATA RESULTS:\nShowing I-70 map")
    text = asyncio.run(pipeline_react._run_direct_skill_route("show I-70 traffic then switch to I-44"))
    assert isinstance(text, str)
    assert "Showing I-70 map" in text


def test_run_direct_skill_route_skips_soft_policy_suggestion(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    text = asyncio.run(pipeline_react._run_direct_skill_route("Show just I-70 traffic data"))
    assert text is None


def test_run_direct_skill_route_returns_none_on_non_guardrail_request(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)
    text = asyncio.run(pipeline_react._run_direct_skill_route("show roads filtered by density"))
    assert text is None


def test_run_direct_skill_route_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(policy_controller, "AGENT_SKILLS_ENABLED", True)

    def _raise(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(policy_controller, "run_unified_sql_query", _raise)
    text = asyncio.run(pipeline_react._run_direct_skill_route("show I-70 traffic then switch to I-44"))
    assert text is None


def test_build_react_agent_omits_memory_and_experience_blocks():
    agent = pipeline_react.build_react_agent(user_query="show traffic")
    assert "[Related Past Experiences]" not in agent.instruction
    assert "RELATED EXPERIENCES" not in agent.instruction
    assert "LEARNED HINTS" not in agent.instruction
    assert "[Learned Behavior Hints]" not in agent.instruction


def test_build_react_agent_includes_policy_guidance_for_soft_skill(monkeypatch):
    monkeypatch.setattr(pipeline_react, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Show just I-70 traffic data")
    agent = pipeline_react.build_react_agent(
        user_query="Show just I-70 traffic data",
        policy_decision=decision,
    )
    assert "[Policy Guidance]" in agent.instruction
    assert "road_corridor_map_scope" in agent.instruction


def test_build_react_agent_includes_grouped_comparison_guidance(monkeypatch):
    monkeypatch.setattr(pipeline_react, "AGENT_SKILLS_ENABLED", True)
    decision = policy_controller.decide_policy("Graph average speed on I-70 vs I-44")
    agent = pipeline_react.build_react_agent(
        user_query="Graph average speed on I-70 vs I-44",
        policy_decision=decision,
    )
    assert "[Policy Guidance]" in agent.instruction
    assert "grouped_comparison" in agent.instruction


def test_build_context_block_includes_active_road_scope(monkeypatch):
    monkeypatch.setattr(pipeline_react, "get_active_session", lambda: "session-1")
    monkeypatch.setattr(
        pipeline_react,
        "get_last_map",
        lambda _sid: {
            "analysis_type": "traffic",
            "roadAggregateFilter": {"road_name": "I 70", "min_points": 1, "limit": 40},
        },
    )
    monkeypatch.setattr(pipeline_react, "get_execution_events", lambda _sid, limit=40: [])
    monkeypatch.setattr(pipeline_react, "get_analysis_context", lambda _sid, limit=4: [])
    block = pipeline_react._build_context_block()
    assert "[Active Road Scope" in block
    assert '"road_name": "I 70"' in block


def test_normalize_followup_user_query_uses_active_scope(monkeypatch):
    monkeypatch.setattr(
        pipeline_react,
        "_active_road_scope",
        lambda: {
            "label": "I 70",
            "roadAggregateFilter": {"road_name": "I 70"},
            "analysis_type": "traffic",
        },
    )
    normalized = pipeline_react._normalize_followup_user_query(
        "Keep the same roads and show top segments by speed"
    )
    assert normalized.startswith("On I 70,")
    assert "top segments by speed" in normalized


def test_normalize_followup_user_query_preserves_explicit_override(monkeypatch):
    monkeypatch.setattr(
        pipeline_react,
        "_active_road_scope",
        lambda: {
            "label": "I 70",
            "roadAggregateFilter": {"road_name": "I 70"},
            "analysis_type": "traffic",
        },
    )
    normalized = pipeline_react._normalize_followup_user_query("Now show I-44")
    assert normalized == "Now show I-44"


def test_ambiguous_incident_corridor_prompt_detected():
    assert pipeline_react._is_ambiguous_incident_corridor_prompt("Show incidents on I-70") is True
    assert pipeline_react._is_ambiguous_incident_corridor_prompt("Show traffic incidents on I-70") is False


def test_run_react_pipeline_clarifies_ambiguous_incident_prompt():
    text = asyncio.run(pipeline_react.run_react_pipeline("Show incidents on I-70"))
    assert "traffic incidents, crashes, or workzones" in text
    assert "I-70" in text


def test_execute_query_rewrites_scoped_top_segment_followup(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        pipeline_react,
        "_active_road_scope",
        lambda: {
            "label": "I 70",
            "roadAggregateFilter": {"road_name": "I 70", "min_points": 1, "limit": 40},
            "analysis_type": "traffic",
        },
    )
    def _capture_plan(plan):
        captured["plan"] = plan
        return '{"rows": []}'

    monkeypatch.setattr(pipeline_react, "run_unified_sql_query", _capture_plan)
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)

    result = asyncio.run(
        pipeline_react.execute_query(
            domain="traffic",
            steps_json='[{"operation":"groupby","params":{"group_by":["road_segment_id"],"aggregations":{"speed_avg":{"fn":"avg","column":"speed"}}}},{"operation":"sort","params":{"sort_by":"speed_avg","order":"desc"}},{"operation":"head","params":{"n":7}}]',
            reasoning="Keep the same roads and show top segments by speed",
        )
    )

    assert '"status": "success"' in result
    plan = captured["plan"]
    assert plan["steps"][0]["operation"] == "filter"
    assert plan["steps"][0]["params"]["conditions"][0]["value"] == "%I 70%"
    assert plan["steps"][1]["operation"] == "groupby"
    assert plan["steps"][1]["params"]["group_by"] == ["road_segment_id"]
    assert plan["steps"][2]["params"]["sort_by"] == "avg_speed_mph"
    assert plan["steps"][3]["params"]["n"] == 7
    assert "Active road scope: I 70" in plan["reasoning"]


def test_execute_query_rewrites_scoped_top_speed_roads_followup(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        pipeline_react,
        "_active_road_scope",
        lambda: {
            "label": "I 70",
            "roadAggregateFilter": {"road_name": "I 70", "min_points": 1, "limit": 40},
            "analysis_type": "traffic",
        },
    )

    def _capture_plan(plan):
        captured["plan"] = plan
        return '{"rows": []}'

    monkeypatch.setattr(pipeline_react, "run_unified_sql_query", _capture_plan)
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)

    asyncio.run(
        pipeline_react.execute_query(
            domain="traffic",
            steps_json='[{"operation":"top_speed_roads","params":{"limit":5}}]',
            reasoning="Keep the same roads and show top segments by speed",
        )
    )

    plan = captured["plan"]
    assert plan["steps"][0]["operation"] == "filter"
    assert plan["steps"][1]["operation"] == "groupby"
    assert plan["steps"][2]["params"]["sort_by"] == "avg_speed_mph"


def test_execute_query_rewrites_explicit_route_top_segment_query(monkeypatch):
    captured = {}

    monkeypatch.setattr(pipeline_react, "_active_road_scope", lambda: {})

    def _capture_plan(plan):
        captured["plan"] = plan
        return '{"rows": []}'

    monkeypatch.setattr(pipeline_react, "run_unified_sql_query", _capture_plan)
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)

    asyncio.run(
        pipeline_react.execute_query(
            domain="traffic",
            steps_json='[{"operation":"top_speed_roads","params":{"limit":5}}]',
            reasoning="Show top segments by speed for I-70",
        )
    )

    plan = captured["plan"]
    assert plan["steps"][0]["params"]["conditions"][0]["value"] == "%I 70%"
    assert plan["steps"][1]["params"]["group_by"] == ["road_segment_id"]
    assert "Active road scope: I-70" in plan["reasoning"]


def test_execute_query_returns_structured_error_payload(monkeypatch):
    monkeypatch.setattr(pipeline_react, "run_unified_sql_query", lambda plan: '{"status":"error","error":"Unsupported column for SQL mode: \\"start_ts\\""}')
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)
    monkeypatch.setattr(pipeline_react, "get_active_session", lambda: "session-1")
    monkeypatch.setattr(pipeline_react, "get_execution_events", lambda _sid, limit=80: [{"type": "react_turn_start"}])

    result = asyncio.run(
        pipeline_react.execute_query(
            domain="traffic",
            steps_json='[{"operation":"filter","params":{"mode":"and","conditions":[{"column":"start_ts","operator":"=","value":"2025-01-01"}]}}]',
            reasoning="Test unsupported column path",
        )
    )

    assert '"status": "error"' in result
    assert '"error_type": "unsupported_column"' in result


def test_execute_query_budget_blocks_third_attempt(monkeypatch):
    monkeypatch.setattr(pipeline_react, "get_active_session", lambda: "session-1")
    monkeypatch.setattr(
        pipeline_react,
        "get_execution_events",
        lambda _sid, limit=80: [
            {"type": "react_turn_start"},
            {"type": "tool_plan", "tool": "execute_query"},
            {"type": "tool_error", "tool": "execute_query", "error": "timed out", "error_type": "timeout"},
            {"type": "tool_plan", "tool": "execute_query"},
            {"type": "tool_error", "tool": "execute_query", "error": "timed out", "error_type": "timeout"},
        ],
    )
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)

    result = asyncio.run(
        pipeline_react.execute_query(
            domain="traffic",
            steps_json='[{"operation":"head","params":{"n":5}}]',
            reasoning="Try one more query",
        )
    )

    assert '"error_type": "tool_budget_exceeded"' in result
    assert '"attempt_limit": 2' in result


def test_run_skill_budget_blocks_second_attempt(monkeypatch):
    monkeypatch.setattr(pipeline_react, "get_active_session", lambda: "session-1")
    monkeypatch.setattr(
        pipeline_react,
        "get_execution_events",
        lambda _sid, limit=80: [
            {"type": "react_turn_start"},
            {"type": "tool_plan", "tool": "run_skill"},
            {"type": "tool_error", "tool": "run_skill", "error": "Missing required skill inputs", "error_type": "execution_error"},
        ],
    )
    monkeypatch.setattr(pipeline_react, "append_execution_event", lambda event: None)

    result = asyncio.run(
        pipeline_react.run_skill(
            "road_corridor_map_scope",
            args_json='{"road_name_or_ref":"I-70"}',
            user_query="Show just I-70 traffic data",
        )
    )

    assert '"error_type": "tool_budget_exceeded"' in result


# ---------------------------------------------------------------------------
# RELY-01: Exception surfacing tests (added for plan 01-02)
# ---------------------------------------------------------------------------

def test_run_conflation_exception_surfaces_error_type(monkeypatch):
    """RELY-01: run_conflation exception includes error_type in event log."""
    test_session = "test-conflation-err-session"
    test_user = "test-conflation-user"
    session_state.set_active_user(test_user)
    session_state.set_active_session(test_session)
    session_state.clear_execution_for_session(test_session)
    monkeypatch.setattr(session_state, "_redis", lambda: None)

    # Monkeypatch run_unified_sql_query to raise
    def _raise(_plan):
        raise RuntimeError("test db connection failed")

    monkeypatch.setattr(pipeline_react, "run_unified_sql_query", _raise)

    result_json = asyncio.run(
        pipeline_react.run_conflation(
            datasets=["crashes", "workzones"],
        )
    )
    result = json.loads(result_json)
    assert result["status"] == "error"
    assert "error_type" in result, "run_conflation error dict must include error_type"

    events = session_state.get_execution_events(test_session, limit=80)
    tool_errors = [e for e in events if e.get("type") == "tool_error" and e.get("tool") == "run_conflation"]
    assert len(tool_errors) >= 1, "run_conflation exception must appear in event log"
    assert "error_type" in tool_errors[-1], "Event must include error_type field"


def test_get_schema_exception_appears_in_event_log(monkeypatch):
    """RELY-01: get_schema exception is logged to execution event log."""
    test_session = "test-schema-err-session"
    test_user = "test-schema-user"
    session_state.set_active_user(test_user)
    session_state.set_active_session(test_session)
    session_state.clear_execution_for_session(test_session)
    monkeypatch.setattr(session_state, "_redis", lambda: None)

    # Monkeypatch the schema fetching internals to raise
    import dynamic_analyst.sql.traffic as traffic_sql
    monkeypatch.setattr(traffic_sql, "get_traffic_domain_schema", lambda dataset_id=None: (_ for _ in ()).throw(
        RuntimeError("schema fetch failed")
    ))

    # Clear schema cache so it doesn't return cached result
    pipeline_react._SCHEMA_CACHE.clear()

    result_json = asyncio.run(pipeline_react.get_schema(domain="traffic"))
    result = json.loads(result_json)
    assert "error" in result

    events = session_state.get_execution_events(test_session, limit=80)
    tool_errors = [e for e in events if e.get("type") == "tool_error" and e.get("tool") == "get_schema"]
    assert len(tool_errors) >= 1, "get_schema exception must appear in event log"


def test_run_react_pipeline_handles_none_route(monkeypatch):
    """RELY-03: (None, None) route still produces a response via generic ReAct."""
    # Force routing to return (None, None) — no skill match
    monkeypatch.setattr(pipeline_react, "choose_policy_skill_route", lambda query, **kw: (None, None))

    # Also force _run_direct_skill_route to return None so we reach the Runner
    async def _no_direct_skill(user_query: str):
        return None

    monkeypatch.setattr(pipeline_react, "_run_direct_skill_route", _no_direct_skill)

    # Mock the ADK Runner so we don't hit Gemini.
    # run_react_pipeline iterates runner.run_async(...) looking for a final event.
    # When the async generator yields nothing, run_react_pipeline falls back to
    # the hardcoded non-empty fallback string.
    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            # Yield nothing — pipeline falls back to its built-in non-empty string.
            return
            yield  # makes this an async generator

    monkeypatch.setattr(pipeline_react, "Runner", FakeRunner)

    result = asyncio.run(pipeline_react.run_react_pipeline(user_query="show crashes"))

    # Key assertion: the request is never dropped — a truthy non-empty response is produced
    assert result, "run_react_pipeline must return a non-empty response even when routing returns (None, None)"
    assert len(result) > 0, "Response must have content"

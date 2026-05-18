from dynamic_analyst.orchestration import sql_dispatcher as dispatcher


def test_normalize_sql_domain_aliases():
    assert dispatcher.normalize_sql_domain("traffic") == "traffic"
    assert dispatcher.normalize_sql_domain("cv") == "traffic"
    assert dispatcher.normalize_sql_domain("hard_braking") == "hard_braking"
    assert dispatcher.normalize_sql_domain("hard-brake") == "hard_braking"
    assert dispatcher.normalize_sql_domain("hb") == "hard_braking"
    assert dispatcher.normalize_sql_domain("crashes") == "crash"
    assert dispatcher.normalize_sql_domain("events") == "crash"
    assert dispatcher.normalize_sql_domain("wzdx") == "workzone"


def test_run_sql_domain_operations_dispatches_to_canonical_runner(monkeypatch):
    seen = {}

    def _mk_runner(name):
        def _runner(query):
            seen["name"] = name
            seen["query"] = query
            return f"{name}-ok"
        return _runner

    monkeypatch.setattr(
        dispatcher,
        "_domain_runners",
        lambda: {
            "traffic": _mk_runner("traffic"),
            "hard_braking": _mk_runner("hard_braking"),
            "crash": _mk_runner("crash"),
            "workzone": _mk_runner("workzone"),
        },
    )

    output = dispatcher.run_sql_domain_operations("accidents", {"steps": []})
    assert output == "crash-ok"
    assert seen == {"name": "crash", "query": {"steps": []}}


def test_run_sql_domain_operations_dispatches_hard_braking_runner(monkeypatch):
    seen = {}

    def _mk_runner(name):
        def _runner(query):
            seen["name"] = name
            seen["query"] = query
            return f"{name}-ok"
        return _runner

    monkeypatch.setattr(
        dispatcher,
        "_domain_runners",
        lambda: {
            "traffic": _mk_runner("traffic"),
            "hard_braking": _mk_runner("hard_braking"),
            "crash": _mk_runner("crash"),
            "workzone": _mk_runner("workzone"),
        },
    )

    output = dispatcher.run_sql_domain_operations("hardbrake", {"steps": [{"operation": "aggregate"}]})
    assert output == "hard_braking-ok"
    assert seen == {"name": "hard_braking", "query": {"steps": [{"operation": "aggregate"}]}}


def test_run_unified_sql_query_supports_inline_payload(monkeypatch):
    calls = {}

    def _fake_run(domain, payload):
        calls["domain"] = domain
        calls["payload"] = payload
        return "done"

    monkeypatch.setattr(dispatcher, "run_sql_domain_operations", _fake_run)

    result = dispatcher.run_unified_sql_query(
        {
            "domain": "traffic",
            "reasoning": "count rows",
            "steps": [{"operation": "aggregate", "params": {"aggregations": {"count": "count"}}}],
        }
    )
    assert result == "done"
    assert calls["domain"] == "traffic"
    assert "steps" in calls["payload"]


def test_run_unified_sql_query_supports_nested_query(monkeypatch):
    calls = {}

    def _fake_run(domain, payload):
        calls["domain"] = domain
        calls["payload"] = payload
        return "nested"

    monkeypatch.setattr(dispatcher, "run_sql_domain_operations", _fake_run)

    result = dispatcher.run_unified_sql_query(
        {
            "domain": "workzone",
            "query": {
                "reasoning": "map all",
                "steps": [{"operation": "generate_map", "params": {"limit": 500}}],
            },
        }
    )
    assert result == "nested"
    assert calls == {
        "domain": "workzone",
        "payload": {"reasoning": "map all", "steps": [{"operation": "generate_map", "params": {"limit": 500}}]},
    }


# ── Conflation domain tests ──────────────────────────────────────────


def test_normalize_sql_domain_conflation_aliases():
    assert dispatcher.normalize_sql_domain("conflation") == "conflation"
    assert dispatcher.normalize_sql_domain("cross_domain") == "conflation"
    assert dispatcher.normalize_sql_domain("spatial_join") == "conflation"
    assert dispatcher.normalize_sql_domain("multi_domain") == "conflation"


def test_conflation_in_supported_domains():
    domains = dispatcher.supported_sql_domains()
    assert "conflation" in domains


def test_run_unified_sql_query_conflation_dispatches(monkeypatch):
    calls = {}

    def _fake_conflation(plan):
        calls["plan"] = plan
        return "conflation-result"

    monkeypatch.setattr(
        dispatcher,
        "_domain_runners",
        lambda: {
            "traffic": lambda q: "traffic-ok",
            "crash": lambda q: "crash-ok",
            "workzone": lambda q: "workzone-ok",
            "conflation": _fake_conflation,
        },
    )

    result = dispatcher.run_unified_sql_query(
        {
            "domain": "conflation",
            "reasoning": "crashes near workzones",
            "datasets": ["crashes", "workzones"],
            "max_distance_meters": 500,
            "generate_map": True,
        }
    )
    assert result == "conflation-result"
    assert calls["plan"]["datasets"] == ["crashes", "workzones"]
    assert calls["plan"]["max_distance_meters"] == 500
    # domain is stripped by run_unified_sql_query
    assert "domain" not in calls["plan"]


def test_run_sql_domain_operations_dispatches_hard_brake_crash_conflation_step(monkeypatch):
    seen = {}

    def _fake_conflation(params):
        seen["params"] = params
        return "hb-crash-conflation-ok"

    monkeypatch.setattr(dispatcher, "_run_crash_hard_brake_conflation", _fake_conflation)

    output = dispatcher.run_sql_domain_operations(
        "hard_braking",
        {
            "steps": [
                {
                    "operation": "conflation_crashes_near_hard_braking",
                    "params": {"distance_m": 275, "time_window_minutes": 90},
                }
            ]
        },
    )
    assert output == "hb-crash-conflation-ok"
    assert seen["params"] == {"distance_m": 275, "time_window_minutes": 90}


def test_run_sql_domain_operations_rejects_hard_brake_crash_conflation_on_non_hb_domain():
    try:
        dispatcher.run_sql_domain_operations(
            "crash",
            {"steps": [{"operation": "conflation_crashes_near_hard_braking", "params": {}}]},
        )
        assert False, "Expected ValueError for non-hard-braking domain"
    except ValueError as exc:
        assert "only supported with domain='hard_braking'" in str(exc)


def test_conflation_bounds_use_config(monkeypatch):
    """CLEAN-02: sql_dispatcher boundary constants come from config.py."""
    from dynamic_analyst.config import (
        SQL_CONFLATION_MIN_DISTANCE_M,
        SQL_CONFLATION_MAX_DISTANCE_M,
        SQL_CONFLATION_MIN_WINDOW_MIN,
        SQL_CONFLATION_MAX_WINDOW_MIN,
    )
    assert dispatcher._MIN_DISTANCE_M == SQL_CONFLATION_MIN_DISTANCE_M
    assert dispatcher._MAX_DISTANCE_M == SQL_CONFLATION_MAX_DISTANCE_M
    assert dispatcher._MIN_WINDOW_MIN == SQL_CONFLATION_MIN_WINDOW_MIN
    assert dispatcher._MAX_WINDOW_MIN == SQL_CONFLATION_MAX_WINDOW_MIN


def test_conflation_bounds_env_override(monkeypatch):
    """CLEAN-02: Constants are overridable via environment variables."""
    monkeypatch.setenv("SQL_CONFLATION_MIN_DISTANCE_M", "5.0")
    # Reload config to pick up env var
    import importlib
    import dynamic_analyst.config as cfg
    importlib.reload(cfg)
    assert cfg.SQL_CONFLATION_MIN_DISTANCE_M == 5.0
    # Restore default
    monkeypatch.delenv("SQL_CONFLATION_MIN_DISTANCE_M")
    importlib.reload(cfg)


def test_run_crash_hard_brake_conflation_normalizes_non_hb_dataset_ref(monkeypatch):
    import sys
    import types

    seen = {}

    def _fake_run_generic_conflation(**kwargs):
        seen.update(kwargs)
        return "ok"

    fake_conflation_ops = types.ModuleType("dynamic_analyst.sql.conflation_ops")
    fake_conflation_ops.run_generic_conflation = _fake_run_generic_conflation
    monkeypatch.setitem(sys.modules, "dynamic_analyst.sql.conflation_ops", fake_conflation_ops)

    fake_conflation_service = types.ModuleType("dynamic_analyst.services.conflation_service")
    fake_conflation_service.is_hard_braking_ref = lambda value: "hard" in str(value).lower()
    monkeypatch.setitem(sys.modules, "dynamic_analyst.services.conflation_service", fake_conflation_service)

    output = dispatcher._run_crash_hard_brake_conflation(
        {
            "crash_dataset_id": "crashes",
            "hard_brake_dataset_id": "mo_week_jul13_19_2025",
            "distance_m": 200,
            "time_window_minutes": 60,
            "time_mode": "window",
            "generate_map": True,
            "limit": 5000,
        }
    )

    assert output == "ok"
    assert seen["left_dataset"] == "crashes"
    assert seen["right_dataset"] == "hard_braking"

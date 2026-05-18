from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "eval_compare.py"
    spec = importlib.util.spec_from_file_location("eval_compare", str(script_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compare_detects_newly_failing_and_pass_rate_delta():
    mod = _load_module()

    base = {
        "totals": {"pass_rate": 1.0, "passed": 2, "failed": 0},
        "latency_ms": {"avg": 100, "p50": 90, "p95": 120},
        "scenario_status": [
            {"id": "s1", "passed": True},
            {"id": "s2", "passed": True},
        ],
    }
    new = {
        "totals": {"pass_rate": 0.5, "passed": 1, "failed": 1},
        "latency_ms": {"avg": 120, "p50": 100, "p95": 160},
        "scenario_status": [
            {"id": "s1", "passed": True},
            {"id": "s2", "passed": False},
        ],
    }

    result = mod.compare(base, new)
    assert result["pass_rate"]["delta"] == -0.5
    assert result["newly_failing"] == ["s2"]
    assert result["latency_ms_delta"]["p95"] == 40.0


def test_evaluate_regression_thresholds_fail_when_exceeded():
    mod = _load_module()
    compare_result = {
        "pass_rate": {"delta": -0.03},
        "latency_ms_delta": {"p95": 275.0},
        "newly_failing": ["a", "b"],
        "added_failed": ["x"],
        "removed_scenarios": ["old_1"],
    }

    gate = mod.evaluate_regression(
        compare_result,
        max_pass_rate_drop=0.01,
        max_newly_failing=0,
        max_added_failed=0,
        max_p95_regression_ms=250,
        allow_removed_scenarios=False,
    )

    assert gate["passed"] is False
    assert len(gate["failures"]) >= 4

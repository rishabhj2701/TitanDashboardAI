#!/usr/bin/env python3
"""Compare two agent eval summary.json files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _scenario_index(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = summary.get("scenario_status") or []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if sid:
            out[sid] = row
    return out


def _delta(new_val: float, old_val: float) -> float:
    return round(float(new_val) - float(old_val), 4)


def compare(base: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    base_totals = base.get("totals") or {}
    new_totals = new.get("totals") or {}
    base_latency = base.get("latency_ms") or {}
    new_latency = new.get("latency_ms") or {}

    base_index = _scenario_index(base)
    new_index = _scenario_index(new)

    newly_failing: List[str] = []
    resolved: List[str] = []
    still_failing: List[str] = []
    added_failed: List[str] = []
    removed_ids: List[str] = []

    for sid, new_row in new_index.items():
        new_passed = bool(new_row.get("passed"))
        base_row = base_index.get(sid)
        if base_row is None:
            if not new_passed:
                added_failed.append(sid)
            continue
        base_passed = bool(base_row.get("passed"))
        if base_passed and not new_passed:
            newly_failing.append(sid)
        elif not base_passed and new_passed:
            resolved.append(sid)
        elif not base_passed and not new_passed:
            still_failing.append(sid)

    for sid in base_index:
        if sid not in new_index:
            removed_ids.append(sid)

    return {
        "base_file": base.get("_source"),
        "new_file": new.get("_source"),
        "pass_rate": {
            "base": float(base_totals.get("pass_rate") or 0.0),
            "new": float(new_totals.get("pass_rate") or 0.0),
            "delta": _delta(float(new_totals.get("pass_rate") or 0.0), float(base_totals.get("pass_rate") or 0.0)),
        },
        "totals": {
            "base_passed": int(base_totals.get("passed") or 0),
            "base_failed": int(base_totals.get("failed") or 0),
            "new_passed": int(new_totals.get("passed") or 0),
            "new_failed": int(new_totals.get("failed") or 0),
        },
        "latency_ms_delta": {
            "avg": _delta(float(new_latency.get("avg") or 0.0), float(base_latency.get("avg") or 0.0)),
            "p50": _delta(float(new_latency.get("p50") or 0.0), float(base_latency.get("p50") or 0.0)),
            "p95": _delta(float(new_latency.get("p95") or 0.0), float(base_latency.get("p95") or 0.0)),
        },
        "newly_failing": sorted(newly_failing),
        "resolved": sorted(resolved),
        "still_failing": sorted(still_failing),
        "added_failed": sorted(added_failed),
        "removed_scenarios": sorted(removed_ids),
    }


def render_text(result: Dict[str, Any]) -> str:
    pass_rate = result["pass_rate"]
    totals = result["totals"]
    latency = result["latency_ms_delta"]

    lines = [
        "Agent Eval Comparison",
        f"Base: {result.get('base_file')}",
        f"New:  {result.get('new_file')}",
        "",
        (
            f"Pass rate: {pass_rate['base']:.2%} -> {pass_rate['new']:.2%} "
            f"(delta {pass_rate['delta']:+.2%})"
        ),
        (
            f"Totals: passed {totals['base_passed']}->{totals['new_passed']}, "
            f"failed {totals['base_failed']}->{totals['new_failed']}"
        ),
        (
            f"Latency delta (ms): avg {latency['avg']:+.2f}, "
            f"p50 {latency['p50']:+.2f}, p95 {latency['p95']:+.2f}"
        ),
        "",
        f"Newly failing: {', '.join(result['newly_failing']) if result['newly_failing'] else 'none'}",
        f"Resolved: {', '.join(result['resolved']) if result['resolved'] else 'none'}",
        f"Still failing: {', '.join(result['still_failing']) if result['still_failing'] else 'none'}",
        f"Added failed scenarios: {', '.join(result['added_failed']) if result['added_failed'] else 'none'}",
        f"Removed scenarios: {', '.join(result['removed_scenarios']) if result['removed_scenarios'] else 'none'}",
    ]
    gate = result.get("gate")
    if isinstance(gate, dict):
        lines.extend(
            [
                "",
                f"Gate: {'PASS' if gate.get('passed') else 'FAIL'}",
            ]
        )
        failures = gate.get("failures") or []
        if failures:
            lines.append("Gate failures:")
            for failure in failures:
                lines.append(f"  - {failure}")
    return "\n".join(lines)


def evaluate_regression(
    result: Dict[str, Any],
    *,
    max_pass_rate_drop: float,
    max_newly_failing: int,
    max_added_failed: int,
    max_p95_regression_ms: float,
    allow_removed_scenarios: bool,
) -> Dict[str, Any]:
    pass_rate_delta = float((result.get("pass_rate") or {}).get("delta") or 0.0)
    newly_failing = list(result.get("newly_failing") or [])
    added_failed = list(result.get("added_failed") or [])
    removed = list(result.get("removed_scenarios") or [])
    p95_delta = float((result.get("latency_ms_delta") or {}).get("p95") or 0.0)

    failures: List[str] = []
    if pass_rate_delta < (-1.0 * float(max_pass_rate_drop)):
        failures.append(
            f"pass_rate dropped by {abs(pass_rate_delta):.4f} (allowed drop={float(max_pass_rate_drop):.4f})"
        )
    if len(newly_failing) > int(max_newly_failing):
        failures.append(
            f"newly_failing count={len(newly_failing)} exceeds max={int(max_newly_failing)}"
        )
    if len(added_failed) > int(max_added_failed):
        failures.append(
            f"added_failed count={len(added_failed)} exceeds max={int(max_added_failed)}"
        )
    if p95_delta > float(max_p95_regression_ms):
        failures.append(
            f"p95 latency regression {p95_delta:.2f}ms exceeds max={float(max_p95_regression_ms):.2f}ms"
        )
    if not allow_removed_scenarios and removed:
        failures.append(f"removed_scenarios present ({len(removed)}): {', '.join(removed)}")

    return {
        "passed": not failures,
        "thresholds": {
            "max_pass_rate_drop": float(max_pass_rate_drop),
            "max_newly_failing": int(max_newly_failing),
            "max_added_failed": int(max_added_failed),
            "max_p95_regression_ms": float(max_p95_regression_ms),
            "allow_removed_scenarios": bool(allow_removed_scenarios),
        },
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two agent eval summary.json files")
    parser.add_argument("--base", required=True, help="Path to baseline summary.json")
    parser.add_argument("--new", required=True, help="Path to new summary.json")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Return non-zero if regression exceeds thresholds.",
    )
    parser.add_argument(
        "--max-pass-rate-drop",
        type=float,
        default=0.0,
        help="Maximum allowed absolute pass-rate drop (0.02 => allow 2% drop).",
    )
    parser.add_argument(
        "--max-newly-failing",
        type=int,
        default=0,
        help="Maximum allowed newly failing scenarios.",
    )
    parser.add_argument(
        "--max-added-failed",
        type=int,
        default=0,
        help="Maximum allowed newly added failed scenarios.",
    )
    parser.add_argument(
        "--max-p95-regression-ms",
        type=float,
        default=0.0,
        help="Maximum allowed p95 latency regression in milliseconds.",
    )
    parser.add_argument(
        "--allow-removed-scenarios",
        action="store_true",
        help="Do not fail gate when baseline scenarios are missing from the new run.",
    )
    parser.add_argument(
        "--gate-report",
        default="",
        help="Optional path to write gate JSON report (only when --fail-on-regression is used).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = _load_json(args.base)
    new = _load_json(args.new)
    base["_source"] = str(Path(args.base).resolve())
    new["_source"] = str(Path(args.new).resolve())
    result = compare(base, new)
    if args.fail_on_regression:
        gate = evaluate_regression(
            result,
            max_pass_rate_drop=args.max_pass_rate_drop,
            max_newly_failing=args.max_newly_failing,
            max_added_failed=args.max_added_failed,
            max_p95_regression_ms=args.max_p95_regression_ms,
            allow_removed_scenarios=args.allow_removed_scenarios,
        )
        result["gate"] = gate
        gate_report = str(args.gate_report or "").strip()
        if gate_report:
            out = Path(gate_report)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8") as f:
                json.dump(gate, f, indent=2)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    if args.fail_on_regression and not bool(result.get("gate", {}).get("passed")):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

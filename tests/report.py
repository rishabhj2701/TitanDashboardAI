"""Generate evaluation report from pytest JSON results.

Usage:
    pytest tests/ --json-report --json-report-file=test_results.json -v
    python tests/report.py test_results.json
"""

import json
import sys
from datetime import datetime, timezone


def generate_report(results_path: str, output_path: str = "eval_report.json"):
    with open(results_path) as f:
        data = json.load(f)

    tests = data.get("tests", [])
    total = len(tests)
    passed = sum(1 for t in tests if t["outcome"] == "passed")
    failed = sum(1 for t in tests if t["outcome"] == "failed")
    errors = sum(1 for t in tests if t["outcome"] == "error")
    skipped = sum(1 for t in tests if t["outcome"] == "skipped")

    # Group by domain (extract from test ID like "test_eval.py::test_scenario[crash_001]")
    by_domain = {}
    for t in tests:
        nodeid = t.get("nodeid", "")
        domain = "unknown"
        if "[" in nodeid:
            param = nodeid.split("[")[1].rstrip("]")
            parts = param.split("_")
            if len(parts) >= 2:
                domain = parts[0]
        by_domain.setdefault(domain, []).append(t)

    domain_scores = {}
    for domain, domain_tests in sorted(by_domain.items()):
        d_total = len(domain_tests)
        d_passed = sum(1 for t in domain_tests if t["outcome"] == "passed")
        domain_scores[domain] = {
            "total": d_total,
            "passed": d_passed,
            "failed": d_total - d_passed,
            "score": f"{d_passed/d_total:.0%}" if d_total > 0 else "N/A",
        }

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "overall_score": f"{passed/total:.0%}" if total > 0 else "N/A",
        },
        "by_domain": domain_scores,
        "failures": [
            {
                "test": t["nodeid"],
                "message": (
                    t.get("call", {}).get("longrepr", "")[:500]
                    if isinstance(t.get("call"), dict) else ""
                ),
            }
            for t in tests
            if t["outcome"] != "passed"
        ],
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary to stdout
    print(f"\n{'='*60}")
    print(f"  EVALUATION REPORT  —  {report['timestamp'][:19]}")
    print(f"{'='*60}")
    print(f"  Overall: {report['summary']['overall_score']}  ({passed}/{total})")
    print(f"  Passed: {passed}  Failed: {failed}  Errors: {errors}  Skipped: {skipped}")
    print(f"{'─'*60}")
    for domain, scores in sorted(domain_scores.items()):
        print(f"  {domain:12s}  {scores['score']:>5s}  ({scores['passed']}/{scores['total']})")
    print(f"{'─'*60}")

    if report["failures"]:
        print(f"\n  FAILURES ({len(report['failures'])}):")
        for f_item in report["failures"][:10]:
            print(f"    - {f_item['test']}")
            if f_item["message"]:
                print(f"      {f_item['message'][:120]}")
    print(f"\n  Full report: {output_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tests/report.py <test_results.json>")
        sys.exit(1)
    generate_report(sys.argv[1])

"""Parametrized evaluation tests across domains.

Run: pytest tests/test_eval.py -v --json-report --json-report-file=test_results.json
"""

import re
import os
import pytest
from conftest import (
    load_scenarios, send_chat, upload_sample_data,
    SCENARIOS_DIR,
)

if os.environ.get("RUN_EVAL", "0").strip().lower() not in {"1", "true", "yes"}:
    pytest.skip(
        "Eval scenarios are opt-in. Set RUN_EVAL=1 to run tests/test_eval.py.",
        allow_module_level=True,
    )

# Terms that MUST NOT appear in responses for generic (non-crash) datasets
CRASH_BLEED_TERMS = [
    r"REPORT FROM CRASH",
    r"Crash dataset_id",
    r"crash specialist",
]

# Patterns indicating the response is leaking internal tool/process details
TOOL_TALK_PATTERNS = [
    r"(?i)the crash specialist",
    r"(?i)the traffic specialist",
    r"(?i)the workzone specialist",
    r"(?i)I (filtered|aggregated|attempted|tried to query)",
    r"(?i)the tool (returned|reported|found)",
    r"(?i)I ran your filter",
    r"(?i)the analysis (shows|returned)",
    r"(?i)SQL",
    r"(?i)dataset_id",
    r"(?i)validator",
]

# Domain → sample data file mapping
DOMAIN_SAMPLE_DATA = {
    "sales": "sales_50.csv",
    "iot": "iot_sensors_50.csv",
    "survey": "survey_responses_50.csv",
}

# Track which domains have been uploaded (module-level cache)
_uploaded_domains: set = set()


def _collect_eval_text(result: dict, response_text: str) -> str:
    parts = [response_text or ""]

    map_sel = result.get("mapSelection")
    if isinstance(map_sel, dict):
        for key in ("label", "analysis_type", "count"):
            val = map_sel.get(key)
            if val is not None:
                parts.append(str(val))

    chart_payload = result.get("chartPayload")
    if isinstance(chart_payload, list):
        for item in chart_payload:
            if isinstance(item, dict):
                for key in ("title", "type", "xLabel", "yLabel"):
                    val = item.get(key)
                    if val is not None:
                        parts.append(str(val))

    return "\n".join(parts)


def _has_numeric_signal(result: dict, response_text: str) -> bool:
    if re.search(r"\d+", response_text or ""):
        return True

    map_sel = result.get("mapSelection")
    if isinstance(map_sel, dict):
        cnt = map_sel.get("count")
        if isinstance(cnt, (int, float)):
            return True

    chart_payload = result.get("chartPayload")
    if isinstance(chart_payload, list):
        for item in chart_payload:
            if not isinstance(item, dict):
                continue
            series = item.get("series")
            if isinstance(series, list):
                for s in series:
                    vals = s.get("values") if isinstance(s, dict) else None
                    if isinstance(vals, list) and any(isinstance(v, (int, float)) for v in vals):
                        return True
    return False


def _has_structured_signal(result: dict, scenario: dict, response_text: str) -> bool:
    analysis_type = (scenario.get("expected_analysis_type") or "").lower()
    lowered = (response_text or "").lower()
    expected_cols = [str(c).lower() for c in (scenario.get("expected_key_columns") or [])]

    if analysis_type in {"distribution", "comparison", "trend"}:
        chart_payload = result.get("chartPayload")
        if isinstance(chart_payload, list) and len(chart_payload) > 0:
            return True
        if expected_cols and any(col in lowered for col in expected_cols):
            return True

    if analysis_type == "raw_table":
        if "|" in response_text or "final data results" in lowered:
            return True
        if _has_numeric_signal(result, response_text):
            return True

    if analysis_type == "summary":
        if _has_numeric_signal(result, response_text):
            return True

    return False


def _all_scenarios():
    """Yield (domain, scenario) tuples for parametrization."""
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        domain = path.stem
        scenarios = load_scenarios(domain)
        for scenario in scenarios:
            yield pytest.param(
                domain, scenario,
                id=scenario.get("id", f"{domain}_{scenarios.index(scenario)}"),
            )


@pytest.mark.parametrize("domain,scenario", list(_all_scenarios()))
def test_scenario(http_client, clean_session, domain, scenario):
    """Run a single scenario and assert all conditions."""
    session_id = clean_session

    # Upload sample data for generic domains (once per domain per module)
    if domain in DOMAIN_SAMPLE_DATA and domain not in _uploaded_domains:
        try:
            upload_sample_data(http_client, session_id, DOMAIN_SAMPLE_DATA[domain])
            _uploaded_domains.add(domain)
        except Exception as e:
            pytest.skip(f"Could not upload {domain} sample data: {e}")

    # Send the question
    result = send_chat(http_client, session_id, scenario["question"])
    response_text = result.get("responseText", "")
    eval_text = _collect_eval_text(result, response_text)
    assertions = scenario.get("assertions", {})
    question_lower = scenario["question"].lower()

    # 1. Response not empty
    if assertions.get("response_not_empty", True):
        assert response_text.strip(), f"Empty response for: {scenario['question']}"

    # 2. No error
    if assertions.get("no_error", True):
        assert "ERROR" not in response_text[:200].upper(), (
            f"Error in response: {response_text[:300]}"
        )

    # 3. Expected narrative patterns (regex)
    for pattern in scenario.get("expected_narrative_patterns", []):
        if re.search(pattern, eval_text, re.IGNORECASE):
            continue
        if _has_structured_signal(result, scenario, response_text):
            continue
        assert False, (
            f"Pattern '{pattern}' not found in response for: {scenario['question']}\n"
            f"Response: {response_text[:500]}"
        )

    # 4. Contains number
    if assertions.get("contains_number"):
        assert _has_numeric_signal(result, response_text), (
            f"No number found in response for: {scenario['question']}"
        )

    # 5. No crash language bleed (for generic datasets)
    if scenario.get("expected_no_crash_bleed") or assertions.get("no_crash_language"):
        for term in CRASH_BLEED_TERMS:
            match = re.search(term, response_text, re.IGNORECASE)
            if match:
                # Allow if the user's question itself mentions "crash"
                if "crash" in question_lower:
                    continue
                assert False, (
                    f"Crash language bleed: '{match.group()}' "
                    f"in response for: {scenario['question']}\n"
                    f"Context: ...{response_text[max(0,match.start()-50):match.end()+50]}..."
                )

    # 6. Map presence
    if assertions.get("has_map") is True:
        map_selection = result.get("mapSelection")
        if "map" in question_lower:
            assert map_selection is not None, (
                f"Expected map but got None for: {scenario['question']}"
            )
        else:
            # Some prompts imply a spatial result but do not explicitly request a map.
            # In those cases, accept either a returned map payload or an explicit map mention.
            assert map_selection is not None or re.search(r"\bmap\b|\bdisplay\b", response_text, re.IGNORECASE), (
                f"Expected map signal but found neither payload nor map mention for: {scenario['question']}"
            )

    # 7. Clarification field
    if assertions.get("has_clarification") is True:
        clar = result.get("clarification")
        assert clar is not None and clar.get("needed"), (
            f"Expected clarification but got None for: {scenario['question']}"
        )

    # 8. Response time
    elapsed = result.get("_elapsed_ms", 0)
    max_ms = scenario.get("max_response_ms", 90000)
    assert elapsed < max_ms, (
        f"Response took {elapsed}ms (max {max_ms}ms) for: {scenario['question']}"
    )

    # 9. Style checks (response quality guardrails)
    style_checks = scenario.get("style_checks", {})
    if style_checks.get("no_tool_talk"):
        for pattern in TOOL_TALK_PATTERNS:
            match = re.search(pattern, response_text)
            if match:
                assert False, (
                    f"Tool talk detected: '{match.group()}' "
                    f"in response for: {scenario['question']}\n"
                    f"Response excerpt: {response_text[:400]}"
                )

    if style_checks.get("max_words"):
        word_count = len(response_text.split())
        max_words = style_checks["max_words"]
        assert word_count <= max_words, (
            f"Response too long: {word_count} words (max {max_words}) "
            f"for: {scenario['question']}"
        )

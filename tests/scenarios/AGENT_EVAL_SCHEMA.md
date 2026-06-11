# Agent Eval Scenario Schema

These scenarios drive `tests/test_agent_eval.py` against live `/api/chat`.

## File Layout

- `tests/scenarios/agent_smoke.json`: fast blocking suite.
- `tests/scenarios/agent_deep.json`: broader manual/nightly suite.
- `tests/scenarios/user_queries.json`: optional user-maintained prompts (loaded when `AGENT_EVAL_INCLUDE_USER_QUERIES=1`).

## Scenario Object

```json
{
  "id": "agent_traffic_i70_001",
  "tier": "smoke",
  "complexity": "simple",
  "domain": "traffic",
  "turns": [
    {
      "user": "Show just I-70 traffic data",
      "assertions": {
        "response_not_empty": true,
        "no_error": true,
        "has_map": true,
        "no_timeout_text": true
      },
      "expected_patterns": ["I.?70|traffic|map|road network"],
      "forbidden_patterns": ["statement timeout|Error executing agent"]
    }
  ],
  "final_assertions": {
    "has_map": true
  }
}
```

## Allowed Values

- `tier`: `smoke` or `deep`
- `complexity`: `simple` or `in_depth`
- `turns`: non-empty array

## Allowed Assertion Keys

- `response_not_empty` (bool)
- `no_error` (bool)
- `has_map` (bool)
- `no_timeout_text` (bool)
- `has_clarification` (bool)
- `contains_number` (bool)
- `expected_patterns_required` (bool)
- `expected_pattern_mode` (`any` or `all`)

`expected_patterns` are regexes matched against `responseText`. They are advisory by default; they become required when:

- `assertions.expected_patterns_required=true`, or
- strict mode is enabled (`AGENT_EVAL_STRICT=1`).

## Multi-turn Scenarios

Use `turns` length > 1. All turns run in the same chat `sessionId` for context carryover.

`final_assertions` apply to the final turn record.

## Custom Query TSV

Pass `--queries-file` to `scripts/eval_agent.sh` using:

```text
complexity<TAB>domain<TAB>prompt
```

Example:

```text
simple	traffic	Show just I-70 data
in_depth	traffic	Show I-70 traffic data for active run then switch to I-44
```

Generated IDs are deterministic: `custom_<line_number>`.

## Suggested Regression Loop

1. Run baseline:

```bash
./scripts/eval_agent.sh \
  --tier deep \
  --scenario-file tests/scenarios/agent_deep.json \
  --out-dir artifacts/evals/baseline_deep
```

2. Tune ReAct, model routing, or deterministic skill changes.

3. Re-run with regression gate:

```bash
./scripts/eval_agent.sh \
  --tier deep \
  --scenario-file tests/scenarios/agent_deep.json \
  --baseline-summary artifacts/evals/baseline_deep/summary.json \
  --max-pass-rate-drop 0.01 \
  --max-newly-failing 0 \
  --max-added-failed 0 \
  --max-p95-regression-ms 250
```

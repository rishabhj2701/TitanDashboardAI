# Iowa CV Segment Aggregates — Agent Context

## Dataset
**Iowa Connected Vehicle Speed Aggregates** — 5-minute bins per route segment (`public.cv_route_segment_stats`).

## Domain routing
- Use **`execute_query` with `domain: "traffic"`** (or `hard_braking` for brake-focused rankings).
- **Do not** use the crash domain for CV speed / hard-braking questions.
- **Crash counts by route** → `domain: "crash"` on the Iowa crash events dataset, **not** this CV dataset.

## Time columns (important)
| Use in filters/groupby | Meaning |
|------------------------|---------|
| `hour` | Hour of day 0–23 (best for “10 PM” → `hour = 22`) |
| `timestamp_5min` | Bin timestamp |
| `start_ts` | Alias for `timestamp_5min` in SQL traffic mode |
| `year`, `month`, `day` | Calendar parts |

Do **not** expect `start_ts` on road-level materialized views that lack time bins — filter on segment table columns above.

## Example: routes with data around 10 PM
```json
[
  {"operation": "filter", "params": {"mode": "and", "conditions": [
    {"column": "hour", "operator": "=", "value": 22}
  ]}},
  {"operation": "groupby", "params": {
    "group_by": ["route_id"],
    "aggregations": {"avg_speed": {"column": "speed_mean_mph", "fn": "mean"}, "bins": "count"}
  }},
  {"operation": "sort", "params": {"sort_by": "bins", "order": "desc"}},
  {"operation": "head", "params": {"n": 15}}
]
```

## Example: 10 slowest routes (map + table — use policy skill, do not ask for date range)
Ask: "Plot the 10 segments with the lowest average speed" → routes by `AVG(speed_mean_mph)` on `cv_route_segment_stats`, excludes 0 mph artifacts, highlights map.

## Example: locate a RAMS route ID on the map
Ask: "where is M573246320E" → filter `route_id = 'M573246320E'` and highlight that road (Iowa RAMS ID, not an interstate name).

## Example: top routes by hard braking
```json
[
  {"operation": "groupby", "params": {
    "group_by": ["route_id"],
    "aggregations": {"hard_brakes": {"column": "decel_03g_sum", "fn": "sum"}}
  }},
  {"operation": "sort", "params": {"sort_by": "hard_brakes", "order": "desc"}},
  {"operation": "head", "params": {"n": 10}}
]
```

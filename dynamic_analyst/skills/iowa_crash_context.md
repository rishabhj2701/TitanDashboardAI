# Iowa Crash Dataset — Agent Context

## Overview
This dataset contains **Iowa statewide crash records** managed by the Iowa DOT.
Approximately 445,000 records covering crashes from January 2018 onward.

## Column Reference

| Column | Type | Description | Notes |
|---|---|---|---|
| `CRASH_KEY` | string | Unique crash identifier | Primary key |
| `LATITUDE` | float | GPS latitude | WGS-84 |
| `LONGITUDE` | float | GPS longitude | WGS-84 |
| `ROUTEID` | string | Iowa RAMS route ID (e.g. `S001920069N`) | Stored as `road_segment_id` |
| `MEASURE` | float | Milepost along the route | Also queryable as `milepost` |
| `CSEVERITY` | int | Crash severity code (KABCO scale) | See severity codes below |
| `CRASH_DATE` | int | Date as YYYYMMDD integer | Parsed to `ts` timestamp |
| `CRASHMONTH` | int | Month number (1–12) | Also queryable as `month` |
| `CRASH_DAY` | int | Day of week (1=Sun … 7=Sat) | Also queryable as `day_of_week` |
| `CRASH_YEAR` | int | Four-digit year | Also queryable as `year` |
| `TIMESTR` | string | Time of crash as HH:MM | Also queryable as `crash_time` |
| `COUNTY` | int | Iowa county code (numeric) | See county codes if needed |

## Severity Codes (CSEVERITY — Iowa KABCO Scale)

| Code | Label |
|---|---|
| `1` | **Fatal** |
| `2` | **Major Injury** |
| `3` | **Minor Injury** |
| `4` | **Property Damage Only** |
| `5` | **Unknown / Not Reported** |

When a user asks about "fatal crashes" → filter `cseverity = 1` or `severity = '1'`.
When a user asks about "injury crashes" → filter `cseverity IN (1, 2, 3)`.
When a user asks about "PDO" or "property damage" → filter `cseverity = 4`.

## Useful Query Patterns

### Crashes by severity
```json
[
  {"operation": "groupby", "params": {"group_by": ["cseverity"], "aggregations": {"count": "count"}}},
  {"operation": "sort", "params": {"sort_by": "count", "order": "desc"}}
]
```

### Fatal crashes on a specific route
```json
[
  {"operation": "filter", "params": {"mode": "and", "conditions": [
    {"column": "cseverity", "operator": "=", "value": "1"},
    {"column": "routeid", "operator": "ilike", "value": "%S001920069N%"}
  ]}},
  {"operation": "generate_map", "params": {"map_type": "points", "limit": 2000}}
]
```

### Crashes by month
```json
[
  {"operation": "groupby", "params": {"group_by": ["month"], "aggregations": {"count": "count"}}},
  {"operation": "sort", "params": {"sort_by": "month", "order": "asc"}}
]
```

### Crashes by year
```json
[
  {"operation": "groupby", "params": {"group_by": ["year"], "aggregations": {"count": "count"}}},
  {"operation": "sort", "params": {"sort_by": "year", "order": "asc"}}
]
```

### Top routes by crash count
Use `routeid`, `road_segment_id`, or `road_name` (all map to Iowa ROUTEID / RAMS route IDs):
```json
[
  {"operation": "groupby", "params": {"group_by": ["routeid"], "aggregations": {"count": "count"}}},
  {"operation": "sort", "params": {"sort_by": "count", "order": "desc"}},
  {"operation": "head", "params": {"n": 15}}
]
```

### Show all crashes on map
```json
[
  {"operation": "generate_map", "params": {"map_type": "points", "limit": 5000}}
]
```

## Iowa County Codes (most common)
County 77 = Polk (Des Moines area), 52 = Johnson (Iowa City), 82 = Scott (Davenport),
7 = Black Hawk (Waterloo), 57 = Linn (Cedar Rapids), 17 = Cerro Gordo, 78 = Pottawattamie.

## Data Quality Notes
- `ROUTEID` follows Iowa RAMS format: prefix letter(s) + route number + direction suffix
- `MEASURE` can be 0 (exact start of segment) — not a data error
- Some rows have empty/space ROUTEID — these still have valid lat/lon coordinates
- `CRASH_DATE` is stored as an integer (e.g. `20180101`) — the system normalizes this to a timestamp

---

# Iowa Connected Vehicle (CV) Segment Aggregates — Agent Context

## Overview
This dataset contains **Iowa Connected Vehicle (CV) speed and deceleration segment aggregates** binned at 5-minute intervals.
It contains ~5.7 million rows of aggregated speeds, deceleration counts, and overspeed bins.

---

## Column Reference (`public.cv_route_segment_stats`)

| Column | Type | Description |
|---|---|---|
| `route_id` | string | Iowa RAMS route ID (e.g. `C007046830E`). Joins directly with crash `ROUTEID` or `road_segment_id` |
| `segment_start_measure` | float | Milepost where this segment starts |
| `timestamp_5min` | timestamp | 5-minute interval bin timestamp |
| `journeyid_count` | int | Total number of vehicle records |
| `journeyid_nunique` | int | Number of unique vehicles in the bin |
| `speed_mean_mph` | float | Mean vehicle speed in MPH |
| `decel_03g_sum` | int | Count of hard deceleration events (hard braking >= 0.3g) |
| `overspeed_10mph` | int | Count of vehicles exceeding speed limit by 10+ mph |
| `overspeed_20mph` | int | Count of vehicles exceeding speed limit by 20+ mph |

---

## Materialized View (`public.cv_road_stats_mv`)
For fast spatial and aggregate lookups, use the pre-compiled `cv_road_stats_mv` view:

| Column | Type | Description |
|---|---|---|
| `road_segment_id` / `way_id` | string | Iowa RAMS route ID |
| `name` | string | Name of the route |
| `avg_speed_mph` | float | Average speed over all time bins |
| `unique_vehicles_total` | int | Total unique vehicles observed |
| `hard_brake_count` | int | Sum of all hard deceleration events |
| `geom_4326` | geometry | Spatial LineString geometry of the route |

---

## Useful Query Patterns

### Relational Join: Average CV Speed on High-Crash Routes
Find the average CV speed and hard braking count for routes that have more than 50 crashes:
```sql
SELECT 
    e.props->>'ROUTEID' as route_id,
    count(e.id) as crash_count,
    avg(s.avg_speed_mph) as avg_speed_mph,
    sum(s.hard_brake_count) as total_hard_brakes
FROM app_data.events e
LEFT JOIN public.cv_road_stats_mv s ON s.road_segment_id = e.props->>'ROUTEID'
WHERE e.dataset_id LIKE 'iowa_crash%'
GROUP BY e.props->>'ROUTEID'
HAVING count(e.id) > 50
ORDER BY crash_count DESC;
```

### Relational Join: Crash Locations with CV Deceleration Spikes
Query routes where fatal or major injury crashes occurred (`cseverity IN ('1', '2')`) and link them to Connected Vehicle average speed and total hard brakes:
```sql
SELECT 
    e.id as crash_id,
    e.props->>'ROUTEID' as route_id,
    e.props->>'severity_label' as severity,
    s.avg_speed_mph,
    s.hard_brake_count
FROM app_data.events e
INNER JOIN public.cv_road_stats_mv s ON s.road_segment_id = e.props->>'ROUTEID'
WHERE e.props->>'CSEVERITY' IN ('1', '2')
ORDER BY s.hard_brake_count DESC
LIMIT 50;
```

### High Hard Braking (Deceleration) Segments
Identify segments with the most deceleration events (hard brakes >= 0.3g):
```sql
SELECT 
    road_segment_id,
    name,
    unique_vehicles_total,
    hard_brake_count,
    avg_speed_mph
FROM public.cv_road_stats_mv
ORDER BY hard_brake_count DESC
LIMIT 20;
```

### Connected Vehicle Layer Map Styling
To display all roads styled by vehicle count or average speed compliance:
- Standard road layers style by average speeds or journey counts from `public.cv_road_stats_mv`.


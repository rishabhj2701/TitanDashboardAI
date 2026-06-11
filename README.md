# TitanBot

An interactive traffic analysis system for connected-vehicle (CV), crash, and workzone data. The user uploads a dataset or asks a question in plain English, and a Google ADK agent answers by running SQL against PostGIS, conflating events to a road network, and rendering results on a Mapbox map.

This document is a writeup of how we put the system together: how our database is built, where the code lives, and how the pieces fit. It is closer to field notes than a polished install guide — we ran most of this on a single Ubuntu VM with a specific dataset and a specific OSM region, so expect to adjust as you go.

The version shown in the demo corresponds to commit `13d1cbf` (`fix(traffic): skip raw cv_points map scan when road stats MV handles the aggregate`) — the last pre-ReAct iteration. The current `react` branch, which this README describes, has since moved the agent runtime to a ReAct loop in `dynamic_analyst/pipeline_react.py`.

## Stack

- **Backend** — FastAPI (`server.py` for the app API, `tile_server.py` for vector tiles).
- **Database** — PostgreSQL 16 with PostGIS, hstore, and btree_gist extensions.
- **Agent** — Google ADK (Gemini) two-layer agent in `dynamic_analyst/`. The dispatcher routes the message; a ReAct inner agent reasons and calls SQL tools.
- **Frontend** — React 19 + TypeScript (Vite) under `src/`, with Mapbox GL + Deck.gl for the map.
- **Conflation pipeline** — `scripts/cv_conflation_pipeline.py` builds the PostGIS layers from raw OSM and CV CSVs.

## Repo layout

```
server.py                          FastAPI app: auth, chat, uploads, analysis
tile_server.py                     FastAPI tile/bbox service for road vector tiles
dynamic_analyst/                   Agent, ReAct pipeline, SQL dispatch, ingestion
  agent.py                           Root dispatcher (TitanBotDispatcher)
  pipeline_react.py                  ReAct inner agent
  sql/                               Domain SQL runners (traffic, crash, workzone, conflation)
  storage/postgis/schema.py          App schema migrations
backend/routers/                   API route modules
src/                               React frontend
db/init/                           Bootstrap SQL for a fresh PostGIS database
db/rebuild_osm_roads_match_and_mv.sql  Helper SQL for road labels + public road stats MV
scripts/cv_conflation_pipeline.py  End-to-end OSM → CV → matched roads pipeline
scripts/legacy_cv_conflation_public_tables.sh  Older single-run flow, kept for reference
tests/sample_data/crashes_sample.csv  Tracked sample for upload smoke tests
```

---

# Backend setup

The backend talks to a PostGIS database that holds three layers:

1. The **OSM road network**, imported once and reduced to a matchable road table.
2. One or more **CV runs** — connected-vehicle datasets loaded into per-run schemas, with each point matched to its nearest road.
3. The **app schema** (`app_data`) where the FastAPI server stores user-uploaded datasets and events.

We built them in this order, since each layer depends on the one before it. None of the parameters below are sacred — match radius, partition counts, highway allowlist, and timezone all worked for our Missouri data and may need different values for yours.

## 1. PostGIS database

We ran Postgres 16 with PostGIS in a Docker container on the VM. The simplest local equivalent is the Compose `localdb` profile, which starts `postgis/postgis:16-3.4` with the extensions we need:

```bash
docker compose --profile localdb up -d postgis
```

If you'd rather run Postgres directly, you'll need `postgresql-16`, `postgis`, and `osm2pgsql` installed on the host, plus a database to point at (we called ours `traffic`). Then apply the bootstrap SQL:

```bash
psql "$POSTGIS_DSN" -f db/init/001_init.sql
psql "$POSTGIS_DSN" -f db/init/002_public_registry.sql
psql "$POSTGIS_DSN" -f db/init/003_auth.sql
```

These create the required extensions and the `app_data` schema (`001`); the `public` registry tables `osm_roads_match`, `cv_runs`, `cv_run_config`, and the public tile dispatch function (`002`); and the `public.users` auth table (`003`). The conflation pipeline populates the registry; the FastAPI server creates and migrates the `app_data.*` tables at runtime — see `dynamic_analyst/storage/postgis/schema.py`.

Set `POSTGIS_DSN` in your `.env` to point at this database. Every example below assumes it is exported in your shell.

## 2. OSM road network

The road network comes from OpenStreetMap. We used Missouri; the pipeline isn't region-specific, but bigger pbf files mean longer imports and a heavier matchable road table.

**The `.osm.pbf` extract.** We pulled `north-america/us/missouri-latest.osm.pbf` from Geofabrik (`https://download.geofabrik.de/`). The pbf is the compressed binary OSM format — Geofabrik publishes country, state, and metro extracts daily.

**`osm2pgsql` import.** This is the command we ran. It populates `public.planet_osm_line`, `public.planet_osm_point`, `public.planet_osm_polygon`, and `public.planet_osm_roads`:

```bash
osm2pgsql --create --slim --hstore --multi-geometry \
  -d traffic -U postgres \
  /path/to/missouri-latest.osm.pbf
```

**Derive the matchable road table.** The pipeline reduces `planet_osm_line` to `public.osm_roads_match` — a single row per `way_id` with name, ref, label, highway class, and geometry in both `4326` (display) and `3857` (meters, for distance math). The DDL is in the schema reference below.

Only roads with a recognized highway class are kept. The default allowlist:

```
motorway, motorway_link, trunk, trunk_link,
primary, primary_link, secondary, secondary_link,
tertiary, tertiary_link, residential, unclassified, living_street
```

Service roads are dropped (`DROP_HIGHWAY_SERVICE=1`). The label column is a stable display string for tile rendering, derived from `ref` if present and `name` otherwise.

`scripts/cv_conflation_pipeline.py` will rebuild this table when `REFRESH_OSM_MATCH=1`. `db/rebuild_osm_roads_match_and_mv.sql` does the same thing standalone.

## 3. CV data into a per-run schema

Each CV dataset (a daily slice, a week, a season) lives in its own schema — for example `cv_mo_20250705` or `cv_mo_202512_winter`. Per-run isolation means you can have multiple datasets registered simultaneously and switch between them without rebuilding anything.

Two registry tables in `public` track them:

- `public.cv_runs` — one row per dataset (`run_id`, `schema_name`, display metadata, point/road counts, time bounds).
- `public.cv_run_config.active_run_id` — points at the run currently exposed through the public MVT wrapper.

The driver script is `scripts/cv_conflation_pipeline.py`. It expects a gzipped CSV with at minimum vehicle id, timestamp (UTC), latitude/longitude, and speed. Acceleration columns (`accx`, `accy`) and a speed limit column unlock hard-braking analysis and speed-limit comparisons. Header naming is normalized — case and underscores don't matter.

Inside a run schema, the script creates:

| Table / view | Purpose |
| --- | --- |
| `cv_points_raw` | Unlogged all-text staging table, one column per CSV header |
| `cv_points` | Typed, range-partitioned by `ts`. Holds `geom_4326` and `geom_3857` |
| `cv_point_match` | Nearest-road match per point (point_id → way_id, dist_m) |
| `cv_hard_brake_events_mv` | Materialized view of hard-braking events (default `accx <= -0.3`) |
| `cv_road_stats_mv` | Per-road aggregates: speeds, vehicle counts, hourly histograms |
| `get_cv_roads_mvt(z, x, y, min_points)` | Vector tile function for the run |

`public.get_cv_roads_mvt` is the public wrapper — it reads `cv_run_config.active_run_id`, looks up the schema in `cv_runs`, and dispatches to that run's tile function. The frontend always calls the public wrapper, so switching the active run rotates the map without a redeploy.

## 4. CV → road matching

For every CV point, find the nearest road in `public.osm_roads_match` whose geometry is within `MATCH_RADIUS_M` meters (default 20m). The match runs in projected EPSG:3857 (meters) using a PostGIS GiST index. The core query is:

```sql
INSERT INTO <run_schema>.cv_point_match (point_id, way_id, dist_m)
SELECT p.id, r.way_id,
       ST_Distance(p.geom_3857, r.geom_3857)
FROM <run_schema>.cv_points p
CROSS JOIN LATERAL (
  SELECT way_id, geom_3857
  FROM public.osm_roads_match
  WHERE ST_DWithin(p.geom_3857, r.geom_3857, 20)
  ORDER BY p.geom_3857 <-> r.geom_3857
  LIMIT 1
) r;
```

`ST_DWithin` prunes candidates with the GiST index; `<-> ... LIMIT 1` is a KNN nearest-neighbor pull. The match is unique per point (PRIMARY KEY on `point_id`), so a single point gets exactly one road or no row at all if nothing was within the radius.

`MATCH_RADIUS_M=20` is a tradeoff. Tighter radii mean cleaner matches on dense urban grids but lose points on highways with imprecise GPS. Looser radii catch more points but risk snapping a frontage-road observation to the parallel highway. Match rates we observed at 20m were 68–96% depending on the dataset's GPS quality.

Coverage is reported at the end of the run:

```
points         total CV points loaded
matched        points that found a road within MATCH_RADIUS_M
match rate     matched / points
```

## 5. Aggregates and tiles

Once points are matched, two materialized views roll the per-point data into per-road and per-event summaries:

- **`cv_road_stats_mv`** — one row per `way_id` with `avg_speed_mph`, `p50/p90_speed_mph`, `speed_limit_mph`, `point_count`, `unique_vehicles_total`, per-hour vehicle histograms (`hourly_unique_vehicles_json`), and time bounds. This is what the road tiles render and what fast road-level questions hit. `AGG_TZ` controls the timezone for time bucketing.
- **`cv_hard_brake_events_mv`** — one row per hard-braking event, joined with the road label/ref/name. Threshold is `HARD_BRAKE_ACCX_THRESHOLD` (default `-0.3` m/s²) with comparison mode `HARD_BRAKE_MODE` (`lte` or `lt`).

Both views are non-concurrent. Rebuilding them is part of the pipeline.

## 6. Running the pipeline

Below is what we ran for the summer-week dataset. Treat it as a worked example, not a magic incantation — paths, identifiers, partition counts, and timezone all need to be set for your data:

```bash
export CV_CSV_GZ="$HOME/conflate_data/cv_one_week.csv.gz"
export RUN_ID="mo_week_jul13_19_2025"
export RUN_SCHEMA="cv_mo_20250713_20250719"
export DISPLAY_NAME="MO CV (Jul 13-19, 2025)"
export DESCRIPTION="Missouri connected-vehicle data, one-week slice"
export SEASON_TAG="summer_2025"
export STATE_CODE="MO"
export IS_VISIBLE=1
export SORT_ORDER=50

# Road table: skip rebuild if osm_roads_match is already current.
export REFRESH_OSM_MATCH=0
export FORCE_OSM_IMPORT=0
export DROP_HIGHWAY_SERVICE=1

# Matching parameters.
export MATCH_RADIUS_M=20
export MATCH_WORKERS=4
export GEOM_WORKERS=6

# Partitioning of cv_points.
export TARGET_POINTS_PER_PART=2000000
export MIN_PARTS=6
export MAX_PARTS=60

# Aggregate views.
export BUILD_HARD_BRAKE=1
export HARD_BRAKE_ACCX_THRESHOLD=-0.3
export HARD_BRAKE_MODE="lte"
export BUILD_STATS=1
export AGG_TZ="America/Chicago"

# Make this the active run for the public tile wrapper.
export ACTIVATE_RUN=1
export CLEANUP_ON_FAIL=1

python scripts/cv_conflation_pipeline.py
```

The script is idempotent on `RUN_SCHEMA`: re-running drops and rebuilds the run cleanly. Set `REFRESH_OSM_MATCH=1` the first time you run it, or whenever you want to refresh the road table from `planet_osm_line`. Set `FORCE_OSM_IMPORT=1` only when you have a new `.osm.pbf` to import.

For reference, on the datasets we ran:

```
1-day    2.1M points    68% match rate    ~2.5 min
1-week   17.9M points   70% match rate    ~21 min
1-month  25.5M points   96% match rate    ~40 min
```

Match rate variance is mostly GPS quality and how complete the OSM road network is for the bounding region.

`scripts/legacy_cv_conflation_public_tables.sh` is the older flow that loaded a single dataset directly into `public.cv_points` / `public.cv_point_match`. Use the Python per-run script for new work — the frontend selector and the public MVT wrapper both assume per-run schemas.

## 7. App schema

The FastAPI backend creates and migrates two tables in `app_data` at startup. You don't author these by hand — the migration code in `dynamic_analyst/storage/postgis/schema.py` runs on boot.

- **`app_data.datasets`** — one row per uploaded or derived dataset. Holds the dataset id, owner, session, name, entity type (crash / workzone / cv / etc.), status, mapping config, provenance, and a JSON stats blob.
- **`app_data.events`** — normalized rows from uploads. Common columns (`ts`, `lat`, `lon`, `geom`, `road_segment_id`, `way_id`, `road_dist_m`) are first-class; everything else from the source row is preserved verbatim in a `props jsonb` column. That is why uploads can have arbitrary extra columns without schema changes.

Both tables are scoped by `(owner_user_id, session_id, dataset_id)` and indexed on those plus `ts` and `way_id`. Geometries are stored in 4326 (display) and 26915 (UTM 15N, meters) — the projected column is what spatial joins and distance queries hit.

Two related tables you'll see at runtime:

- `app_data.dataset_codebook` — column-level metadata captured during ingestion.
- `app_data.user_cv_run_config` — per-user override of which CV run is active for that user's session, separate from the global `public.cv_run_config`.

## Schema reference

DDL for the load-bearing tables in our live DB. The pipeline script generates these; the exact column order and a few defaults can drift depending on which env vars you set when the pipeline runs and which OSM attributes your region exposes.

### `public.osm_roads_match`

```sql
CREATE TABLE public.osm_roads_match (
  way_id    bigint PRIMARY KEY,
  name      text,
  highway   text,
  ref       text,
  label     text,
  geom_4326 geometry(MultiLineString, 4326),
  geom_3857 geometry(MultiLineString, 3857)
);
CREATE INDEX osm_roads_match_geom3857_gist ON public.osm_roads_match USING gist (geom_3857);
CREATE INDEX osm_roads_match_ref_idx       ON public.osm_roads_match (ref);
CREATE INDEX osm_roads_match_label_idx     ON public.osm_roads_match (label);
```

### `public.cv_runs`, `public.cv_run_config`

```sql
CREATE TABLE public.cv_runs (
  run_id              text PRIMARY KEY,
  schema_name         text NOT NULL,
  display_name        text,
  description         text,
  season_tag          text,
  state_code          text,
  road_count          bigint,
  point_count         bigint,
  ts_start            timestamptz,
  ts_end              timestamptz,
  bbox_4326           geometry(Polygon, 4326),
  stats_refreshed_at  timestamptz,
  is_visible          boolean NOT NULL DEFAULT true,
  sort_order          integer NOT NULL DEFAULT 0,
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.cv_run_config (
  id              integer PRIMARY KEY,
  active_run_id   text REFERENCES public.cv_runs(run_id)
);
```

### `<run_schema>.cv_points`, `cv_point_match`

```sql
CREATE TABLE <run_schema>.cv_points (
  id               bigint NOT NULL,
  ts               timestamptz NOT NULL,
  lon              double precision NOT NULL,
  lat              double precision NOT NULL,
  speed            double precision,
  vehicle_id       text,
  speed_limit_mph  double precision,
  accx             double precision,
  accy             double precision,
  attrs            jsonb,
  geom_4326        geometry(Point, 4326),
  geom_3857        geometry(Point, 3857),
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE UNLOGGED TABLE <run_schema>.cv_point_match (
  point_id  bigint PRIMARY KEY,
  way_id    bigint NOT NULL,
  dist_m    double precision
);
```

### `app_data.datasets`, `app_data.events`

```sql
CREATE TABLE app_data.datasets (
  dataset_id      text PRIMARY KEY,
  owner_user_id   text,
  session_id      text NOT NULL,
  user_id         text,
  name            text NOT NULL,
  entity_type     text,
  status          text NOT NULL DEFAULT 'ready',
  mapping         jsonb DEFAULT '{}'::jsonb,
  provenance      jsonb DEFAULT '[]'::jsonb,
  stats           jsonb DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_data.events (
  id              bigserial PRIMARY KEY,
  dataset_id      text NOT NULL,
  owner_user_id   text,
  session_id      text NOT NULL,
  user_id         text,
  ts              timestamptz,
  lat             double precision,
  lon             double precision,
  geom            geometry(Point, 4326),
  geom_m          geometry(Point, 26915),
  geom_feature    geometry(Geometry, 4326),
  geom_feature_m  geometry(Geometry, 26915),
  road_segment_id text,
  way_id          bigint,
  road_dist_m     double precision,
  road_conf       double precision,
  props           jsonb NOT NULL DEFAULT '{}'::jsonb
);
```

---

# Running the app

Once the database has the road network and at least one CV run (or just the `app_data` schema if you only want to test uploads + chat), bring up the rest of the stack:

```bash
cp .env.example .env
# Edit .env and set GOOGLE_API_KEY, VITE_MAPBOX_TOKEN, JWT_SECRET, POSTGIS_DSN.

docker compose up --build
# Or, for a self-contained local DB:
docker compose --profile localdb up --build
```

The frontend lands at `http://localhost:8080`. Nginx proxies `/api/*` to the FastAPI app on `:8000` and `/tiles/*` plus `/api/roads/bbox` to the tile service on `:8001`.

## First-run walkthrough

The fastest way to confirm the stack is healthy end-to-end:

1. **Register.** On the login page, click **Register** and create a local email/password account. Any properly-formed email works (e.g. `you@local.test`) — it's only used as a login key in the local `public.users` table. The Google / GitHub OAuth buttons require your own OAuth client credentials configured for `http://localhost:8080` redirects; skip them for getting started.
2. **Upload a sample.** From the upload panel, pick `tests/sample_data/crashes_sample.csv`. Ingestion auto-detects `entity_type=crash` and maps lat/lon, severity, road, and date columns. You'll see a warning in the `app_api` logs: `Road matching skipped (public.osm_roads_match does not exist)` — that's expected if you haven't done the OSM import; the upload completes anyway, just without `way_id` populated on each event row.
3. **Ask the agent something.** In the chat panel: `"show all crashes"`. The agent responds in ~5 seconds with a one-line summary plus a map spec (`has_map: true` in the chat-response log). The `cv_sample.csv` and `workzones_sample.json` samples in the same dir exercise the other two upload paths.
4. **Map render.** If the map area in the UI is blank, you didn't set `VITE_MAPBOX_TOKEN` in `.env` *before* the `docker compose up --build` ran. The Vite token is baked into the web image at build time, not read at runtime — rebuild the `web` container after setting the token.

You may also see a `POST /api/cv/aggregate-roads` returning 500 in the logs. That's the frontend auto-firing a road-aggregate query for the map overlay, which fails when there's no CV run loaded. It doesn't block uploads, chat, or anything else — the road overlay just won't render. Importing OSM and running the conflation pipeline is what populates that path.

## Required environment

```
POSTGIS_DSN          PostGIS connection string
GOOGLE_API_KEY       Gemini API key for the agent
VITE_MAPBOX_TOKEN    Mapbox token for the frontend map
JWT_SECRET           Must be set and non-default in production
```

## Useful optional environment

```
APP_DATA_SCHEMA=app_data         App schema name; rarely changed
APP_ENV=development              "production"/"prod" enforces auth + CORS strictness
REDIS_URL=redis://redis:6379/0   Enables Redis-backed session state
AGENT_MODEL_BACKEND=google       google | litellm
AGENT_MODEL_DEFAULT=gemini-2.5-flash
VITE_USE_ROAD_TILES=1            Enable road tile rendering in the frontend
VITE_ROAD_TILES_DATASET=<run_id> Default CV run for tiles
```

OpenRouter / LiteLLM mode:

```
AGENT_MODEL_BACKEND=litellm
AGENT_MODEL_DEFAULT=openrouter/openai/gpt-5-mini
OPENROUTER_API_KEY=...
```

Vite build-time variables (`VITE_*`) are baked into the web image. Rebuild the web container after changing them.

---

# CSV upload

Once you log in, the upload panel takes CSV, JSON, GeoJSON-style, XML, and WZDx workzone JSON. Each upload becomes one row in `app_data.datasets` and N rows in `app_data.events`.

The ingestion code (`dynamic_analyst/ingestion/`) auto-detects common columns by name — it looks for latitude/longitude, timestamp, road, severity, speed, vehicle id, and a few others. Anything it doesn't recognize is preserved verbatim under `props`. So the rule is: as long as the file has the fields the analysis needs, the column names don't have to be exact.

For a smoke test, three tracked samples in `tests/sample_data/` cover the upload formats — `crashes_sample.csv`, `cv_sample.csv`, and `workzones_sample.json`. Coordinates are in the St. Louis area so they fall on real Missouri roads if you've imported the Missouri OSM extract. They exercise auth, upload, ingestion, session isolation, map rendering, and agent queries against `app_data.events` without needing any CV/road infrastructure beyond a running Postgres. See `tests/sample_data/README.md` for what each file covers and what extra steps unlock road-level matching.

If the upload includes lat/lon and `public.osm_roads_match` is populated, ingestion also runs a per-event nearest-road match using the same algorithm as the CV pipeline (KNN + radius), writing `way_id` and `road_dist_m` back onto each event row.

---

# Agent system

The agent lives in `dynamic_analyst/` and runs on Google ADK (Gemini). The chat-loop architecture has gone through one significant rewrite since the demo. This section walks through what the demo version looked like, what the current `react` branch looks like, and where we've been pulling things over time.

## Pre-ReAct (demo at commit `13d1cbf`)

The demo flow was a fixed three-stage pipeline in `dynamic_analyst/pipeline.py`:

1. **Plan.** A `pure_planner` LLM agent (`get_pure_planner_agent`) read the user query plus the dataset catalog and emitted a JSON plan — a list of steps with operation type, target dataset, filters, group-bys, and spatial scope.
2. **Execute.** `run_unified_sql_query` in `dynamic_analyst/orchestration/sql_dispatcher.py` walked the plan steps and dispatched each one to a domain runner under `dynamic_analyst/sql/`. The result was rows plus optional map/chart specs.
3. **Explain.** An `explainer` LLM agent (`get_explainer_agent`) read the rows and wrote a natural-language summary.

It worked, but the agent never saw tool errors and never iterated. If the planner emitted a step that hit a missing column or returned zero rows, the explainer just narrated the empty result. There was also a layer of "adaptive memory" / experience replay trying to learn from prior sessions, which was adding complexity without moving the eval needle and was removed in `4f85fb8`.

`pipeline.py` is still in the tree as a reference for what the demo flow looked like, but nothing on the live agent path imports it any more — the dispatcher in `agent.py` pulls `get_pipeline_tool` from `pipeline_react`. If we cut it, this section is the only thing it's still wired into.

## ReAct loop (current)

Two layers, both rebuilt per-request with fresh session context:

1. **Root dispatcher** (`dynamic_analyst/agent.py`, `TitanBotDispatcher`) handles each conversational turn. It exposes a single tool, `run_data_analysis`, which it invokes when the user asks something analytical and skips for plain chat.
2. **ReAct inner agent** (`dynamic_analyst/pipeline_react.py`, `TitanBotReAct`) has five tools: `list_datasets`, `get_schema`, `execute_query`, `run_conflation`, `list_skills`. It reasons, calls a tool, observes the result, and loops until it can answer or hits the step budget.

Compared to the linear pipeline, the ReAct loop:

- Sees structured tool errors (`_classify_tool_error`) and gets guidance on what to try next instead of dead-ending on a bad query.
- Has per-tool attempt budgets (`_tool_attempt_limit`) so it can't burn the whole step budget thrashing on one broken call.
- Carries forward road-scope state (`_active_road_scope`) and recent-query context across turns, so follow-ups like "break that down by hour" land on the right corridor without restating it.
- Picks up route refs like `I-44` or `MO-13` directly from the user's wording (`_extract_route_refs`).

`execute_query` and `run_conflation` still go through the same `sql_dispatcher.py` and the same domain runners — the SQL layer didn't change, only what's driving it.

## Skills

Skills live in `dynamic_analyst/skills/`. They used to be small LLM agents wrapping specific intents. After `389e2f5` they're deterministic query recipes — parameterized SQL templates the agent invokes when an intent is recognizable and the parameters are extractable. The agent calls `list_skills` to see what's available, and `_choose_direct_skill_route` can short-circuit the loop entirely when a query unambiguously maps to a recipe (e.g. "top hard-braking roads"). The point is to keep the LLM out of decisions that are really lookups.

## Model routing

`dynamic_analyst/modeling.py` binds each agent role to its own model. Four roles:

| Role | Used by | Env var |
| --- | --- | --- |
| `dispatcher` | Root chat agent | `AGENT_MODEL_DISPATCHER` |
| `react` | Inner ReAct loop | `AGENT_MODEL_REACT` |
| `planner` | Classic pre-ReAct planner | `AGENT_MODEL_PLANNER` |
| `explainer` | Classic pre-ReAct explainer | `AGENT_MODEL_EXPLAINER` |

Any role without its own env var falls back to `AGENT_MODEL_DEFAULT` (defaults to `gemini-2.5-flash`). `AGENT_MODEL_BACKEND` is `google` for native Gemini or `litellm` to route through OpenRouter or another provider — `get_agent_model(role)` materializes the right wrapper.

The point of routing is cost vs. capability. A dispatcher that just decides "is this a chat turn or an analysis turn" doesn't need a frontier model; the ReAct loop, where errors compound, often does. `get_model_routing_overview()` is surfaced on each chat response under `agentRunMetadata.routing`, so the frontend (and the eval harness) can see which model handled which role for that turn. A built-in price book (`BUILTIN_PRICE_BOOK_USD_PER_1M`) covers the Gemini family, and usage telemetry from each ADK event flows through `collect_usage_from_event` into per-role token counters that the eval scripts use for cost attribution.

## Direction

A few things we've been pulling on:

- **Bounded loops over open ones.** The ReAct step budget and per-tool attempt limits are deliberate — the goal is "answers a finite question, doesn't loop on its own confusion." Most of the recent work in `pipeline_react.py` has been about classifying failure modes so the budget is spent on real recovery, not retries of the same broken call.
- **Push deterministic work out of the LLM.** Skills became recipes. The next step on this axis is more pre-ReAct intent matching so easy queries skip planning entirely.
- **Fewer moving parts in agent state.**  Per-session context (active map, recent queries, road scope) is centralized in `session_state.py` and that's the whole story.
- **Eval-driven changes.** Anything touching prompts, the tool surface, or model routing should move smoke and deep eval numbers before it ships. To exercise the agent end-to-end against a running backend:

```bash
./scripts/eval_agent.sh --tier smoke --base-url http://localhost:8000
./scripts/eval_agent.sh --tier deep  --auth-email user@example.com --auth-password secret
```

Per-session state (active maps, execution events, current dataset context) lives in `dynamic_analyst/session_state.py`, scoped as `{user_id}::{session_id}` to enforce isolation. Redis is used when `REDIS_URL` is set; otherwise it falls back to in-memory dicts that are fine for single-process local dev but not for multi-worker deployments.

---

# Troubleshooting

Feel free to message Sowmya or Connor if you have any questions or troubles. (cpjtdx@missouri.edu sghmy@missouri.edu)

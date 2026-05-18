#!/usr/bin/env python3
"""Build a per-run CV/PostGIS schema for the traffic app.

This script is a cleaned version of the remote Jupyter/VM pipeline used to turn
large gzipped CV CSV exports plus OSM road data into the tables consumed by the
frontend CV run selector and road tile APIs.

It intentionally runs psql through `sudo docker exec` because that is how the
remote Postgres instance was operated. Adapt `psql_base_cmd` if your database is
not running in a local Docker container.
"""

from __future__ import annotations

import datetime as dt
import gzip
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager


STEP_TIMES: dict[str, float] = {}


def log(msg: str) -> None:
    print(msg, flush=True)


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise SystemExit(f"[FATAL] Missing required env var: {name}")
    return "" if value is None else str(value)


def fmt_dur(seconds: float) -> str:
    seconds = float(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    if hours:
        return f"{hours}h {minutes}m {rest:.1f}s"
    if minutes:
        return f"{minutes}m {rest:.1f}s"
    return f"{rest:.2f}s"


@contextmanager
def timed(step_name: str):
    started = time.time()
    log(f"[TIMER] START {step_name}")
    try:
        yield
    finally:
        elapsed = time.time() - started
        STEP_TIMES[step_name] = STEP_TIMES.get(step_name, 0.0) + elapsed
        log(f"[TIMER] END   {step_name}  ({fmt_dur(elapsed)})")


def sh(cmd: list[str] | str, input_text: str | None = None, check: bool = True) -> str:
    cmd_list = shlex.split(cmd) if isinstance(cmd, str) else cmd
    proc = subprocess.run(
        cmd_list,
        input=input_text.encode("utf-8") if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}):\n{cmd_list}\n\nOUTPUT:\n{out}")
    return out


def psql_base_cmd(container: str, db: str, dbuser: str, at: bool = False) -> list[str]:
    cmd = [
        "sudo",
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        dbuser,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if at:
        cmd.append("-At")
    cmd += ["-f", "-"]
    return cmd


def psql(container: str, db: str, dbuser: str, sql: str) -> str:
    return sh(psql_base_cmd(container, db, dbuser), input_text=sql)


def psql_at(container: str, db: str, dbuser: str, sql: str) -> str:
    return sh(psql_base_cmd(container, db, dbuser, at=True), input_text=sql).strip()


def sql_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit(f"[FATAL] Unsafe identifier: {name}")
    return name


def sql_lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_text_list(items: list[str]) -> str:
    return ", ".join(sql_lit(item) for item in items)


def normalize_col(col: str) -> str:
    out = col.strip().strip('"').strip("'").lower()
    out = re.sub(r"[^a-z0-9]+", "_", out)
    out = re.sub(r"_+", "_", out).strip("_")
    if not out:
        out = "col"
    if out[0].isdigit():
        out = "_" + out
    return out


def make_unique(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for col in cols:
        count = seen.get(col, 0)
        out.append(col if count == 0 else f"{col}_{count + 1}")
        seen[col] = count + 1
    return out


def detect_header(cv_csv_gz: str) -> list[str]:
    with gzip.open(cv_csv_gz, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        header = fh.readline().strip("\n\r")
    if not header:
        raise SystemExit("[FATAL] Header line empty. Is the file valid gzip CSV?")
    cols = make_unique([normalize_col(col) for col in header.split(",")])
    log(f"[INFO] Header: {header}")
    log(f"[INFO] Normalized cols: {cols}")
    return cols


def pick_first(cols: list[str], candidates: list[str]) -> str | None:
    available = set(cols)
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def parse_ts(value: str) -> dt.datetime:
    text = value.strip()
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    if re.search(r"([+-]\d{2})$", text):
        text += ":00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return dt.datetime.fromisoformat(text)


def fmt_ts(value: dt.datetime) -> str:
    return value.isoformat()


def copy_gz_to_table(container: str, db: str, dbuser: str, gz_path: str, table_fqn: str) -> str:
    cmd = [
        "sudo",
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        dbuser,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        f"\\copy {table_fqn} FROM STDIN WITH (FORMAT csv, HEADER true)",
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert proc.stdin is not None
        with gzip.open(gz_path, "rb") as fh:
            while True:
                chunk = fh.read(8 * 1024 * 1024)
                if not chunk:
                    break
                proc.stdin.write(chunk)
        proc.stdin.close()
        assert proc.stdout is not None
        out = proc.stdout.read().decode("utf-8", errors="replace")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"COPY failed ({rc}). OUTPUT:\n{out}")
        return out
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass


def wait_pg_ready(container: str, db: str, dbuser: str, timeout_s: int = 300) -> None:
    started = time.time()
    while True:
        proc = subprocess.run(
            ["sudo", "docker", "exec", container, "pg_isready", "-U", dbuser, "-d", db],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return
        if time.time() - started > timeout_s:
            raise SystemExit("[FATAL] Timed out waiting for Postgres to be ready")
        time.sleep(1)


def assert_schema_not_exists(container: str, db: str, dbuser: str, run_schema: str) -> None:
    hit = psql_at(
        container,
        db,
        dbuser,
        f"""
SELECT 1
FROM pg_namespace
WHERE nspname = {sql_lit(run_schema)}
LIMIT 1;
""",
    )
    if hit == "1":
        raise SystemExit(f"[FATAL] RUN_SCHEMA already exists: {run_schema}")


def assert_run_id_not_exists(container: str, db: str, dbuser: str, run_id: str) -> None:
    exists = psql_at(
        container,
        db,
        dbuser,
        "SELECT to_regclass('public.cv_runs') IS NOT NULL;",
    ).lower()
    if exists not in {"t", "true", "1"}:
        return
    hit = psql_at(
        container,
        db,
        dbuser,
        f"SELECT 1 FROM public.cv_runs WHERE run_id = {sql_lit(run_id)} LIMIT 1;",
    )
    if hit == "1":
        raise SystemExit(f"[FATAL] RUN_ID already exists: {run_id}")


def get_partitions(container: str, db: str, dbuser: str, run_schema: str) -> list[str]:
    out = psql_at(
        container,
        db,
        dbuser,
        f"""
SELECT inhrelid::regclass::text
FROM pg_inherits
WHERE inhparent='{run_schema}.cv_points'::regclass
  AND inhrelid::regclass::text LIKE '{run_schema}.cv_points_p%'
ORDER BY 1;
""",
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def cleanup_failed_run(container: str, db: str, dbuser: str, run_id: str, run_schema: str) -> None:
    log(f"[CLEANUP] Removing failed run run_id={run_id} schema={run_schema}")
    psql(
        container,
        db,
        dbuser,
        f"""
DELETE FROM public.cv_runs WHERE run_id = {sql_lit(run_id)};
DROP SCHEMA IF EXISTS {run_schema} CASCADE;
""",
    )


def refresh_osm_roads(container: str, db: str, dbuser: str, highway_filter_sql: str, drop_service: int) -> None:
    psql(
        container,
        db,
        dbuser,
        f"""
CREATE SCHEMA IF NOT EXISTS public;

DROP TABLE IF EXISTS public.osm_roads CASCADE;
CREATE TABLE public.osm_roads (
  way_id bigint PRIMARY KEY,
  name text,
  highway text,
  geom_4326 geometry(MultiLineString, 4326),
  geom_3857 geometry(MultiLineString, 3857)
);

INSERT INTO public.osm_roads (way_id, name, highway, geom_4326, geom_3857)
SELECT
  osm_id::bigint AS way_id,
  name,
  highway,
  ST_Transform(ST_Multi(way), 4326) AS geom_4326,
  ST_Multi(way) AS geom_3857
FROM (
  SELECT DISTINCT ON (osm_id)
    osm_id,
    name,
    highway,
    way
  FROM public.planet_osm_line
  WHERE way IS NOT NULL
    AND highway IS NOT NULL
  ORDER BY osm_id, ST_Length(way) DESC NULLS LAST
) t;

CREATE INDEX IF NOT EXISTS osm_roads_geom3857_gist ON public.osm_roads USING GIST (geom_3857);
ANALYZE public.osm_roads;

DROP TABLE IF EXISTS public.osm_roads_match CASCADE;
CREATE TABLE public.osm_roads_match AS
SELECT *
FROM public.osm_roads
WHERE highway IS NOT NULL
  AND ({highway_filter_sql})
  AND (CASE WHEN {drop_service}::int = 1 THEN highway <> 'service' ELSE true END);

ALTER TABLE public.osm_roads_match ADD PRIMARY KEY (way_id);
ALTER TABLE public.osm_roads_match
  ADD COLUMN IF NOT EXISTS ref text,
  ADD COLUMN IF NOT EXISTS label text;

UPDATE public.osm_roads_match r
SET
  name = COALESCE(NULLIF(r.name, ''), NULLIF(l.name, '')),
  ref = COALESCE(NULLIF(r.ref, ''), NULLIF(split_part(l.ref, ';', 1), '')),
  highway = COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')),
  label = COALESCE(
    NULLIF(r.label, ''),
    NULLIF(split_part(l.ref, ';', 1), ''),
    NULLIF(COALESCE(NULLIF(r.name, ''), NULLIF(l.name, '')), ''),
    CASE
      WHEN COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')) IS NOT NULL
        THEN initcap(replace(COALESCE(NULLIF(r.highway, ''), NULLIF(l.highway, '')), '_', ' ')) || ' #' || r.way_id::text
      ELSE 'Way ' || r.way_id::text
    END
  )
FROM public.planet_osm_line l
WHERE l.osm_id = r.way_id;

UPDATE public.osm_roads_match r
SET label = COALESCE(
  NULLIF(r.label, ''),
  NULLIF(r.ref, ''),
  NULLIF(r.name, ''),
  CASE
    WHEN NULLIF(r.highway, '') IS NOT NULL
      THEN initcap(replace(r.highway, '_', ' ')) || ' #' || r.way_id::text
    ELSE 'Way ' || r.way_id::text
  END
)
WHERE r.label IS NULL OR r.label = '';

CREATE INDEX IF NOT EXISTS osm_roads_match_geom3857_gist ON public.osm_roads_match USING GIST (geom_3857);
CREATE INDEX IF NOT EXISTS osm_roads_match_ref_idx ON public.osm_roads_match (ref);
CREATE INDEX IF NOT EXISTS osm_roads_match_label_idx ON public.osm_roads_match (label);
ANALYZE public.osm_roads_match;
""",
    )


def build_road_stats(container: str, db: str, dbuser: str, run_schema: str, agg_tz: str) -> None:
    agg_tz_sql = agg_tz.replace("'", "''")
    psql(
        container,
        db,
        dbuser,
        f"""
DROP MATERIALIZED VIEW IF EXISTS {run_schema}.cv_road_stats_mv;

CREATE MATERIALIZED VIEW {run_schema}.cv_road_stats_mv AS
WITH matched AS (
  SELECT
    m.way_id,
    p.ts,
    p.speed,
    m.dist_m,
    p.speed_limit_mph,
    p.vehicle_id,
    (p.ts AT TIME ZONE '{agg_tz_sql}')::date AS local_day,
    date_trunc('hour', p.ts AT TIME ZONE '{agg_tz_sql}') AS local_hour
  FROM {run_schema}.cv_point_match m
  JOIN {run_schema}.cv_points p ON p.id = m.point_id
),
core AS (
  SELECT
    r.way_id,
    r.name,
    r.ref,
    r.label,
    r.highway,
    r.geom_3857,
    r.geom_4326,
    COUNT(*)::bigint AS point_count,
    AVG(m.speed)::float8 AS avg_speed_mph,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY m.speed) AS p50_speed_mph,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY m.speed) AS p90_speed_mph,
    AVG(m.speed_limit_mph)::float8 AS speed_limit_mph,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY m.speed_limit_mph)
      FILTER (WHERE m.speed_limit_mph IS NOT NULL) AS speed_limit_p50_mph,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY m.speed_limit_mph)
      FILTER (WHERE m.speed_limit_mph IS NOT NULL) AS speed_limit_p90_mph,
    MIN(m.ts) AS start_ts,
    MAX(m.ts) AS end_ts,
    AVG(m.dist_m)::float8 AS avg_match_dist_m,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY m.dist_m) AS p90_match_dist_m
  FROM matched m
  JOIN public.osm_roads_match r ON r.way_id = m.way_id
  GROUP BY r.way_id, r.name, r.ref, r.label, r.highway, r.geom_3857, r.geom_4326
),
vid AS (
  SELECT way_id, vehicle_id, local_day, local_hour
  FROM matched
  WHERE vehicle_id IS NOT NULL
),
vehicle_total AS (
  SELECT way_id, COUNT(DISTINCT vehicle_id)::bigint AS unique_vehicles_total
  FROM vid
  GROUP BY way_id
),
day_unique AS (
  SELECT way_id, local_day, COUNT(DISTINCT vehicle_id)::bigint AS unique_vehicles_day
  FROM vid
  GROUP BY way_id, local_day
),
hour_unique AS (
  SELECT way_id, local_hour, COUNT(DISTINCT vehicle_id)::bigint AS unique_vehicles_hour
  FROM vid
  GROUP BY way_id, local_hour
),
day_stats AS (
  SELECT way_id, AVG(unique_vehicles_day)::float8 AS avg_unique_vehicles_per_day
  FROM day_unique
  GROUP BY way_id
),
hour_stats AS (
  SELECT way_id, AVG(unique_vehicles_hour)::float8 AS avg_unique_vehicles_per_hour
  FROM hour_unique
  GROUP BY way_id
),
hour_of_day_unique AS (
  SELECT
    way_id,
    local_day,
    EXTRACT(HOUR FROM local_hour)::integer AS hour_of_day,
    COUNT(DISTINCT vehicle_id)::bigint AS unique_vehicles_hour_of_day
  FROM vid
  GROUP BY way_id, local_day, EXTRACT(HOUR FROM local_hour)::integer
),
hour_of_day_avg AS (
  SELECT
    way_id,
    hour_of_day,
    AVG(unique_vehicles_hour_of_day)::float8 AS avg_unique_vehicles_hour_of_day
  FROM hour_of_day_unique
  GROUP BY way_id, hour_of_day
),
way_ids AS (
  SELECT way_id FROM core
),
hour_profile AS (
  SELECT
    w.way_id,
    jsonb_object_agg(
      h.hour_of_day::text,
      COALESCE(a.avg_unique_vehicles_hour_of_day, 0)::float8
      ORDER BY h.hour_of_day
    ) AS hourly_unique_vehicles_json
  FROM way_ids w
  CROSS JOIN generate_series(0, 23) AS h(hour_of_day)
  LEFT JOIN hour_of_day_avg a
    ON a.way_id = w.way_id
   AND a.hour_of_day = h.hour_of_day
  GROUP BY w.way_id
)
SELECT
  c.*,
  COALESCE(v.unique_vehicles_total, 0)::bigint AS unique_vehicles_total,
  COALESCE(ds.avg_unique_vehicles_per_day, 0)::float8 AS avg_unique_vehicles_per_day,
  COALESCE(hs.avg_unique_vehicles_per_hour, 0)::float8 AS avg_unique_vehicles_per_hour,
  COALESCE(hp.hourly_unique_vehicles_json, '{{}}'::jsonb) AS hourly_unique_vehicles_json
FROM core c
LEFT JOIN vehicle_total v ON v.way_id = c.way_id
LEFT JOIN day_stats ds ON ds.way_id = c.way_id
LEFT JOIN hour_stats hs ON hs.way_id = c.way_id
LEFT JOIN hour_profile hp ON hp.way_id = c.way_id;

CREATE UNIQUE INDEX IF NOT EXISTS cv_road_stats_mv_way_id_idx ON {run_schema}.cv_road_stats_mv (way_id);
CREATE INDEX IF NOT EXISTS cv_road_stats_mv_geom3857_gist ON {run_schema}.cv_road_stats_mv USING gist (geom_3857);
CREATE INDEX IF NOT EXISTS cv_road_stats_mv_point_count_idx ON {run_schema}.cv_road_stats_mv (point_count);
CREATE INDEX IF NOT EXISTS cv_road_stats_mv_label_idx ON {run_schema}.cv_road_stats_mv (label);
ANALYZE {run_schema}.cv_road_stats_mv;

CREATE OR REPLACE FUNCTION {run_schema}.get_cv_roads_mvt(
  z int,
  x int,
  y int,
  min_points int DEFAULT 20
)
RETURNS bytea
LANGUAGE sql
STABLE
AS $$
WITH bounds AS (
  SELECT ST_TileEnvelope(z, x, y) AS geom
),
mvtgeom AS (
  SELECT
    way_id,
    COALESCE(NULLIF(label,''), NULLIF(ref,''), NULLIF(name,''), 'Way ' || way_id::text) AS road_name,
    name,
    ref,
    label,
    highway,
    point_count,
    avg_speed_mph,
    p50_speed_mph,
    p90_speed_mph,
    speed_limit_mph,
    speed_limit_p50_mph,
    speed_limit_p90_mph,
    avg_match_dist_m,
    p90_match_dist_m,
    unique_vehicles_total,
    avg_unique_vehicles_per_day,
    avg_unique_vehicles_per_hour,
    ST_AsMVTGeom(
      CASE
        WHEN z <= 7 THEN ST_Simplify(geom_3857, 200)
        WHEN z <= 9 THEN ST_Simplify(geom_3857, 80)
        WHEN z <= 11 THEN ST_Simplify(geom_3857, 30)
        ELSE geom_3857
      END,
      bounds.geom, 4096, 64, true
    ) AS geom
  FROM {run_schema}.cv_road_stats_mv, bounds
  WHERE geom_3857 && bounds.geom
    AND point_count >= min_points
)
SELECT ST_AsMVT(mvtgeom, 'roads', 4096, 'geom')
FROM mvtgeom;
$$;

CREATE OR REPLACE FUNCTION public.get_cv_roads_mvt(
  z int,
  x int,
  y int,
  min_points int DEFAULT 20
)
RETURNS bytea
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  s text;
  dynsql text;
  out bytea;
BEGIN
  SELECT r.schema_name INTO s
  FROM public.cv_run_config c
  JOIN public.cv_runs r ON r.run_id = c.active_run_id
  WHERE c.id = 1;

  IF s IS NULL THEN
    RAISE EXCEPTION 'No active CV run set. Update public.cv_run_config.active_run_id';
  END IF;

  dynsql := format('SELECT %I.get_cv_roads_mvt($1,$2,$3,$4)', s);
  EXECUTE dynsql INTO out USING z, x, y, min_points;
  RETURN out;
END;
$$;
""",
    )


def main() -> None:
    STEP_TIMES.clear()
    cv_csv_gz = env("CV_CSV_GZ", required=True)
    run_schema = sql_ident(env("RUN_SCHEMA", required=True))
    run_id = env("RUN_ID", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    container = env("CONTAINER", default="postgis_conflate")
    db = env("DB", default="traffic")
    dbuser = env("DBUSER", default="postgres")

    osm_pbf = env("OSM_PBF", default="")
    force_osm = int(env("FORCE_OSM_IMPORT", default="0"))
    refresh_osm = int(env("REFRESH_OSM_MATCH", default="1"))
    osm_pghost = env("OSM_PGHOST", default="127.0.0.1")
    osm_pgport = env("OSM_PGPORT", default="5434")
    drop_service = int(env("DROP_HIGHWAY_SERVICE", default="1"))

    match_radius_m = float(env("MATCH_RADIUS_M", default="20"))
    match_workers = int(env("MATCH_WORKERS", default="4"))
    geom_workers = int(env("GEOM_WORKERS", default="2"))
    target_points = int(env("TARGET_POINTS_PER_PART", default="2000000"))
    min_parts = int(env("MIN_PARTS", default="6"))
    max_parts = int(env("MAX_PARTS", default="60"))

    build_stats = int(env("BUILD_STATS", default="1"))
    activate_run = int(env("ACTIVATE_RUN", default="1"))
    build_hard_brake = int(env("BUILD_HARD_BRAKE", default="1"))
    hard_brake_accx_threshold = float(env("HARD_BRAKE_ACCX_THRESHOLD", default="-0.3"))
    hard_brake_mode = env("HARD_BRAKE_MODE", default="lte").strip().lower()
    agg_tz = env("AGG_TZ", default="America/Chicago")
    attrs_mode = env("ATTRS_MODE", default="minimal").strip().lower()
    attrs_keep = [
        normalize_col(x)
        for x in env("ATTRS_KEEP", default="vehicleid,vehicletype,speedlimitmph,accx,accy,roadname").split(",")
        if x.strip()
    ]

    display_name = env("DISPLAY_NAME", default=run_id)
    description = env("DESCRIPTION", default="")
    season_tag = env("SEASON_TAG", default="")
    state_code = env("STATE_CODE", default="")
    is_visible = int(env("IS_VISIBLE", default="1"))
    sort_order = int(env("SORT_ORDER", default="0"))
    cleanup_on_fail = int(env("CLEANUP_ON_FAIL", default="1"))

    default_allow = [
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "residential",
        "unclassified",
        "living_street",
    ]
    default_block = ["footway", "path", "cycleway", "steps", "bridleway", "pedestrian", "corridor", "track"]
    allow = [x.strip() for x in env("OSM_HIGHWAY_ALLOW", default="").split(",") if x.strip()]
    block = [x.strip() for x in env("OSM_HIGHWAY_BLOCK", default="").split(",") if x.strip()]
    allow_mode = env("OSM_HIGHWAY_ALLOW_MODE", default="allowlist").strip().lower()
    if allow_mode == "allowlist":
        highway_filter_sql = f"highway IN ({sql_text_list(allow or default_allow)})"
    else:
        highway_filter_sql = f"highway NOT IN ({sql_text_list(block or default_block)})"

    try:
        with timed("TOTAL_RUN"):
            log(f"[INFO] CV_CSV_GZ={cv_csv_gz}")
            log(f"[INFO] RUN_SCHEMA={run_schema}")
            log(f"[INFO] CONTAINER={container} DB={db} DBUSER={dbuser}")
            log(f"[INFO] MATCH_RADIUS_M={match_radius_m} MATCH_WORKERS={match_workers} GEOM_WORKERS={geom_workers}")

            if not os.path.isfile(cv_csv_gz):
                raise SystemExit(f"[FATAL] CV_CSV_GZ not found: {cv_csv_gz}")

            with timed("wait_pg_ready"):
                wait_pg_ready(container, db, dbuser)
            with timed("guard_run_schema"):
                assert_schema_not_exists(container, db, dbuser, run_schema)
            with timed("guard_run_id"):
                assert_run_id_not_exists(container, db, dbuser, run_id)
            with timed("extensions"):
                psql(container, db, dbuser, "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS hstore;")

            if force_osm:
                with timed("osm_import"):
                    if not osm_pbf or not os.path.isfile(osm_pbf):
                        raise SystemExit(f"[FATAL] FORCE_OSM_IMPORT=1 but OSM_PBF not found: {osm_pbf}")
                    sh(
                        [
                            "osm2pgsql",
                            "-H",
                            osm_pghost,
                            "-P",
                            str(osm_pgport),
                            "-U",
                            dbuser,
                            "-d",
                            db,
                            "--slim",
                            "--cache",
                            "8000",
                            "--hstore",
                            osm_pbf,
                        ]
                    )

            if refresh_osm:
                with timed("osm_refresh"):
                    refresh_osm_roads(container, db, dbuser, highway_filter_sql, drop_service)
                    count = psql_at(container, db, dbuser, "SELECT COUNT(*) FROM public.osm_roads_match;")
                    log(f"[INFO] osm_roads_match rows: {count}")

            with timed("cv_runs_upsert"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
CREATE SCHEMA IF NOT EXISTS {run_schema};

CREATE TABLE IF NOT EXISTS public.cv_runs (
  run_id text PRIMARY KEY,
  schema_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  display_name text,
  description text,
  season_tag text,
  state_code text,
  road_count bigint,
  point_count bigint,
  ts_start timestamptz,
  ts_end timestamptz,
  bbox_4326 geometry(Polygon, 4326),
  stats_refreshed_at timestamptz,
  is_visible boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS public.cv_run_config (
  id integer PRIMARY KEY,
  active_run_id text REFERENCES public.cv_runs(run_id)
);

INSERT INTO public.cv_run_config (id, active_run_id)
VALUES (1, NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.cv_runs (
  run_id, schema_name, display_name, description, season_tag, state_code, is_visible, sort_order
)
VALUES (
  {sql_lit(run_id)}, {sql_lit(run_schema)}, {sql_lit(display_name)}, {sql_lit(description)},
  {sql_lit(season_tag)}, {sql_lit(state_code)}, {'true' if is_visible else 'false'}, {sort_order}
);
""",
                )

            with timed("detect_header"):
                cols = detect_header(cv_csv_gz)
                ts_col = pick_first(cols, ["timestamputc", "timestamp_utc", "timestamp", "ts", "time"])
                lon_col = pick_first(cols, ["longitude", "lon", "lng"])
                lat_col = pick_first(cols, ["latitude", "lat"])
                slon_col = pick_first(cols, ["snappedlongitude", "snapped_longitude", "snapped_lon", "snappedlng"])
                slat_col = pick_first(cols, ["snappedlatitude", "snapped_latitude", "snapped_lat"])
                speed_col = pick_first(cols, ["speedmph", "speed_mph", "speed"])
                vehicle_id_col = pick_first(cols, ["vehicleid", "vehicle_id", "vehicleid_hash", "vehicle"])
                speed_limit_col = pick_first(cols, ["speedlimitmph", "speed_limit_mph", "speed_limit", "speedlimit"])
                accx_col = pick_first(cols, ["accx", "acc_x"])
                accy_col = pick_first(cols, ["accy", "acc_y"])
                log(
                    "[INFO] Detected: "
                    f"ts={ts_col} lon={lon_col} lat={lat_col} slon={slon_col} slat={slat_col} "
                    f"speed={speed_col} vehicle_id={vehicle_id_col} speed_limit={speed_limit_col} accx={accx_col} accy={accy_col}"
                )
                if not ts_col:
                    raise SystemExit(f"[FATAL] Could not find timestamp column in header: {cols}")
                if not ((slon_col and slat_col) or (lon_col and lat_col)):
                    raise SystemExit("[FATAL] Could not find lon/lat or snappedlon/snappedlat columns")

            with timed("create_raw_table"):
                col_defs = ",\n  ".join(f"{col} text" for col in cols)
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
DROP TABLE IF EXISTS {run_schema}.cv_points_raw CASCADE;
CREATE UNLOGGED TABLE {run_schema}.cv_points_raw (
  {col_defs}
);
""",
                )

            with timed("copy_raw"):
                copy_gz_to_table(container, db, dbuser, cv_csv_gz, f"{run_schema}.cv_points_raw")

            raw_count = int(psql_at(container, db, dbuser, f"SELECT COUNT(*) FROM {run_schema}.cv_points_raw;") or "0")
            log(f"[INFO] Loaded raw rows: {raw_count}")
            if raw_count == 0:
                raise SystemExit("[FATAL] Raw rowcount is 0 after COPY")

            parts = max(min_parts, (raw_count + target_points - 1) // target_points)
            parts = max(1, min(max_parts, parts))
            log(f"[INFO] Chosen N_PARTS={parts}")

            with timed("create_typed_partition"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
DROP TABLE IF EXISTS {run_schema}.cv_points CASCADE;
CREATE TABLE {run_schema}.cv_points (
  id bigserial,
  ts timestamptz NOT NULL,
  lon double precision NOT NULL,
  lat double precision NOT NULL,
  speed double precision,
  vehicle_id text,
  speed_limit_mph double precision,
  accx double precision,
  accy double precision,
  attrs jsonb,
  geom_4326 geometry(Point, 4326),
  geom_3857 geometry(Point, 3857),
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
CREATE TABLE {run_schema}.cv_points_default PARTITION OF {run_schema}.cv_points DEFAULT;
""",
                )
                bounds = psql_at(
                    container,
                    db,
                    dbuser,
                    f"""
WITH ts AS (
  SELECT ({ts_col})::timestamptz AS ts
  FROM {run_schema}.cv_points_raw
  WHERE {ts_col} IS NOT NULL AND {ts_col} <> ''
),
b AS (
  SELECT ts, ntile({parts}) OVER (ORDER BY ts) AS k
  FROM ts
)
SELECT k||'|'||min(ts)||'|'||max(ts)
FROM b
GROUP BY k
ORDER BY k;
""",
                )
                rows = [line for line in bounds.splitlines() if line.strip()]
                if not rows:
                    raise SystemExit("[FATAL] Could not compute timestamp partitions.")
                starts: list[dt.datetime] = []
                prev: dt.datetime | None = None
                maxs: list[dt.datetime] = []
                for row in rows:
                    _, tmin_s, tmax_s = row.split("|", 2)
                    tmin = parse_ts(tmin_s)
                    if prev is not None and tmin <= prev:
                        tmin = prev + dt.timedelta(microseconds=1)
                    starts.append(tmin)
                    prev = tmin
                    maxs.append(parse_ts(tmax_s))
                final_end = maxs[-1] + dt.timedelta(microseconds=1)
                ends = starts[1:] + [final_end]
                for idx, (start, end) in enumerate(zip(starts, ends), start=1):
                    psql(
                        container,
                        db,
                        dbuser,
                        f"""
CREATE TABLE IF NOT EXISTS {run_schema}.cv_points_p{idx}
PARTITION OF {run_schema}.cv_points
FOR VALUES FROM ({sql_lit(fmt_ts(start))}) TO ({sql_lit(fmt_ts(end))});
""",
                    )

            with timed("insert_typed_points"):
                if slon_col and slat_col:
                    lon_expr = f"COALESCE(NULLIF({slon_col},''), NULLIF({lon_col},''))" if lon_col else f"NULLIF({slon_col},'')"
                    lat_expr = f"COALESCE(NULLIF({slat_col},''), NULLIF({lat_col},''))" if lat_col else f"NULLIF({slat_col},'')"
                else:
                    lon_expr = f"NULLIF({lon_col},'')"
                    lat_expr = f"NULLIF({lat_col},'')"
                speed_expr = f"NULLIF({speed_col},'')::double precision" if speed_col else "NULL::double precision"
                vehicle_expr = f"NULLIF(BTRIM({vehicle_id_col}), '')" if vehicle_id_col else "NULL::text"
                speed_limit_expr = f"NULLIF({speed_limit_col},'')::double precision" if speed_limit_col else "NULL::double precision"
                accx_expr = f"NULLIF({accx_col},'')::double precision" if accx_col else "NULL::double precision"
                accy_expr = f"NULLIF({accy_col},'')::double precision" if accy_col else "NULL::double precision"
                keep_keys = [key for key in attrs_keep if key in set(cols)]
                if attrs_mode == "full":
                    attrs_expr = "to_jsonb(r)"
                elif attrs_mode == "none":
                    attrs_expr = "NULL::jsonb"
                elif keep_keys:
                    attrs_expr = "jsonb_strip_nulls(jsonb_build_object(" + ", ".join(
                        f"'{key}', NULLIF(BTRIM({key}), '')" for key in keep_keys
                    ) + "))"
                else:
                    attrs_expr = "'{}'::jsonb"
                log(f"[INFO] ATTRS_KEEP active keys: {keep_keys}")
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
BEGIN;
SET LOCAL synchronous_commit = off;
SET LOCAL work_mem = '256MB';
INSERT INTO {run_schema}.cv_points (
  ts, lon, lat, speed, vehicle_id, speed_limit_mph, accx, accy, attrs
)
SELECT
  ({ts_col})::timestamptz,
  ({lon_expr})::double precision,
  ({lat_expr})::double precision,
  {speed_expr},
  {vehicle_expr},
  {speed_limit_expr},
  {accx_expr},
  {accy_expr},
  {attrs_expr}
FROM {run_schema}.cv_points_raw r
WHERE {ts_col} IS NOT NULL AND {ts_col} <> ''
  AND {lon_expr} IS NOT NULL AND {lon_expr} <> ''
  AND {lat_expr} IS NOT NULL AND {lat_expr} <> '';
COMMIT;
""",
                )
                typed_count = psql_at(container, db, dbuser, f"SELECT COUNT(*) FROM {run_schema}.cv_points;")
                log(f"[INFO] Inserted typed points: {typed_count}")
                partitions = get_partitions(container, db, dbuser, run_schema)

            with timed("build_geoms"):
                def geom_update(partition: str) -> str:
                    psql(
                        container,
                        db,
                        dbuser,
                        f"""
UPDATE {partition}
SET geom_4326 = ST_SetSRID(ST_MakePoint(lon, lat), 4326),
    geom_3857 = ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 3857)
WHERE geom_4326 IS NULL;
""",
                    )
                    return partition

                with ThreadPoolExecutor(max_workers=max(1, geom_workers)) as pool:
                    for future in as_completed([pool.submit(geom_update, part) for part in partitions]):
                        future.result()

            with timed("create_indexes"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
CREATE INDEX IF NOT EXISTS cv_points_ts_brin ON {run_schema}.cv_points USING BRIN (ts);
CREATE INDEX IF NOT EXISTS cv_points_geom3857_gist ON {run_schema}.cv_points USING GIST (geom_3857);
ANALYZE {run_schema}.cv_points;
""",
                )

            with timed("match_points"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
DROP TABLE IF EXISTS {run_schema}.cv_point_match CASCADE;
CREATE UNLOGGED TABLE {run_schema}.cv_point_match (
  point_id bigint NOT NULL,
  way_id bigint NOT NULL,
  dist_m double precision
);
""",
                )

                def match_part(partition: str) -> str:
                    psql(
                        container,
                        db,
                        dbuser,
                        f"""
INSERT INTO {run_schema}.cv_point_match (point_id, way_id, dist_m)
SELECT
  p.id,
  r.way_id,
  ST_Distance(p.geom_3857, r.geom_3857) AS dist_m
FROM ONLY {partition} p
JOIN LATERAL (
  SELECT way_id, geom_3857
  FROM public.osm_roads_match r
  WHERE ST_DWithin(p.geom_3857, r.geom_3857, {match_radius_m})
  ORDER BY p.geom_3857 <-> r.geom_3857
  LIMIT 1
) r ON true;
""",
                    )
                    return partition

                with ThreadPoolExecutor(max_workers=max(1, match_workers)) as pool:
                    for future in as_completed([pool.submit(match_part, part) for part in partitions]):
                        future.result()

            with timed("match_indexes"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
ALTER TABLE {run_schema}.cv_point_match ADD CONSTRAINT cv_point_match_pk PRIMARY KEY (point_id);
CREATE INDEX IF NOT EXISTS cv_point_match_way_id_idx ON {run_schema}.cv_point_match (way_id);
ANALYZE {run_schema}.cv_point_match;
""",
                )

            if build_hard_brake:
                with timed("hard_brake_mv"):
                    operator = ">" if hard_brake_mode == "gt" else "<="
                    psql(
                        container,
                        db,
                        dbuser,
                        f"""
DROP MATERIALIZED VIEW IF EXISTS {run_schema}.cv_hard_brake_events_mv;
CREATE MATERIALIZED VIEW {run_schema}.cv_hard_brake_events_mv AS
SELECT
  p.id AS point_id,
  p.ts,
  p.lon,
  p.lat,
  p.speed,
  p.accx,
  p.accy,
  m.way_id,
  m.dist_m,
  r.label,
  r.ref,
  r.name,
  r.highway,
  p.geom_4326,
  p.geom_3857,
  p.attrs
FROM {run_schema}.cv_points p
JOIN {run_schema}.cv_point_match m ON m.point_id = p.id
JOIN public.osm_roads_match r ON r.way_id = m.way_id
WHERE p.accx {operator} {hard_brake_accx_threshold}
  AND p.accx IS NOT NULL;
CREATE INDEX IF NOT EXISTS cv_hard_brake_mv_ts_brin ON {run_schema}.cv_hard_brake_events_mv USING BRIN (ts);
CREATE INDEX IF NOT EXISTS cv_hard_brake_mv_way_id_idx ON {run_schema}.cv_hard_brake_events_mv (way_id);
CREATE INDEX IF NOT EXISTS cv_hard_brake_mv_geom3857_gist ON {run_schema}.cv_hard_brake_events_mv USING GIST (geom_3857);
ANALYZE {run_schema}.cv_hard_brake_events_mv;
""",
                    )

            with timed("update_cv_runs_stats"):
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
WITH s AS (
  SELECT
    COUNT(*)::bigint AS point_count,
    MIN(ts) AS ts_start,
    MAX(ts) AS ts_end,
    ST_SetSRID(ST_Extent(geom_4326), 4326)::geometry(Polygon,4326) AS bbox_4326
  FROM {run_schema}.cv_points
),
r AS (
  SELECT COUNT(DISTINCT way_id)::bigint AS road_count
  FROM {run_schema}.cv_point_match
)
UPDATE public.cv_runs x
SET
  point_count = s.point_count,
  ts_start = s.ts_start,
  ts_end = s.ts_end,
  bbox_4326 = s.bbox_4326,
  road_count = r.road_count,
  stats_refreshed_at = now()
FROM s, r
WHERE x.run_id = {sql_lit(run_id)};
""",
                )

            log("[STEP] Coverage report")
            print(
                psql(
                    container,
                    db,
                    dbuser,
                    f"""
SELECT
  (SELECT COUNT(*) FROM {run_schema}.cv_points) AS total_points,
  (SELECT COUNT(*) FROM {run_schema}.cv_point_match) AS matched_points,
  ROUND(100.0 * (SELECT COUNT(*) FROM {run_schema}.cv_point_match) / NULLIF((SELECT COUNT(*) FROM {run_schema}.cv_points),0), 2) AS pct_matched;
""",
                )
            )

            if build_stats:
                with timed("cv_road_stats_mv_and_functions"):
                    build_road_stats(container, db, dbuser, run_schema, agg_tz)

            if activate_run:
                with timed("activate_run"):
                    psql(container, db, dbuser, f"UPDATE public.cv_run_config SET active_run_id={sql_lit(run_id)} WHERE id=1;")

            log("[DONE]")
            total = 0.0
            for name, value in sorted(STEP_TIMES.items(), key=lambda item: item[1], reverse=True):
                total += value
                log(f"  - {name}: {fmt_dur(value)}")
            log(f"  = TOTAL: {fmt_dur(total)}")
            log(f"[DONE] Run schema: {run_schema}")
    except Exception as exc:
        log(f"[ERROR] {exc}")
        if cleanup_on_fail:
            try:
                cleanup_failed_run(container, db, dbuser, run_id, run_schema)
            except Exception as cleanup_exc:
                log(f"[WARN] Cleanup also failed: {cleanup_exc}")
        raise


if __name__ == "__main__":
    main()

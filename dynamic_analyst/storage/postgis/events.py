from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import psycopg2
import psycopg2.extras

from ...data_registry import _safe_value
from .detection import profile_columns
from .geometry_utils import (
    _extract_geojson_geometry,
    _geometry_centroid_lon_lat,
    _infer_geometry_col,
    _infer_lat_lon,
    _infer_ts_col,
    _parse_datetime_series,
    _to_float_or_none,
)
from .table_names import APP_EVENTS


logger = logging.getLogger("adk_server")


def _normalize_date_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    if isinstance(parsed, datetime):
        return parsed.date().isoformat()
    return parsed.strftime("%Y-%m-%d")


def _normalize_time_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, datetime):
        return parsed.strftime("%H:%M:%S")
    return parsed.strftime("%H:%M:%S")


def _owner_clause(sid, uid):
    """Return (where_fragment, param) for user_id or session_id scoping."""
    if uid:
        return "user_id = %s", uid
    return "session_id = %s", sid


def preview_events(
    dataset_id: str,
    limit: int,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    crash_timezone: str,
) -> List[Dict[str, Any]]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    with conn_factory() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT
                ts,
                lat,
                lon,
                road_segment_id,
                way_id,
                road_dist_m,
                road_conf,
                (ts AT TIME ZONE %s) AS ts_local,
                props
            FROM {APP_EVENTS}
            WHERE dataset_id=%s AND {owner_where}
            ORDER BY id ASC
            LIMIT %s
            """,
            (crash_timezone, dataset_id, owner_val, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def ingest_events_df(
    dataset_id: str,
    df: pd.DataFrame,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    ensure_core_tables_fn,
    ensure_events_upload_columns_fn,
    crash_timezone: str,
) -> Dict[str, Any]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    lat_col, lon_col = _infer_lat_lon(df)
    geom_col = _infer_geometry_col(df)
    ts_series = None
    ts_col: Optional[str] = None

    def _find_col(name: str) -> Optional[str]:
        for c in df.columns:
            if c.lower() == name:
                return c
        return None

    date_col = _find_col("accident_date") or _find_col("event_date") or _find_col("crash_date")
    time_col = _find_col("accident_time") or _find_col("event_time") or _find_col("crash_time")
    if date_col and time_col:
        datetime_formats = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
        date_series = _parse_datetime_series(df[date_col], formats=datetime_formats)
        time_series = _parse_datetime_series(df[time_col], formats=datetime_formats)
        if date_series.notna().any() and time_series.notna().any():
            date_local = date_series.dt.tz_localize(None) if date_series.dt.tz is not None else date_series
            time_local = time_series.dt.tz_localize(None) if time_series.dt.tz is not None else time_series
            base = date_local.dt.normalize()
            hours = time_local.dt.hour.fillna(0).astype("int64")
            minutes = time_local.dt.minute.fillna(0).astype("int64")
            seconds = time_local.dt.second.fillna(0).astype("int64")
            ts_series = (
                base
                + pd.to_timedelta(hours, unit="h")
                + pd.to_timedelta(minutes, unit="m")
                + pd.to_timedelta(seconds, unit="s")
            )
            ts_series = ts_series.dt.tz_localize(crash_timezone, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
            ts_col = f"{date_col}+{time_col}"

    if ts_series is None:
        ts_col = _infer_ts_col(df)
        if ts_col:
            ts_series = _parse_datetime_series(df[ts_col])
    ts_source_col = ts_col if ts_col and ts_col in df.columns else None

    rows = []
    geometry_rows = 0
    for i, r in df.iterrows():
        lat = _to_float_or_none(r.get(lat_col)) if lat_col else None
        lon = _to_float_or_none(r.get(lon_col)) if lon_col else None

        geom_obj = _extract_geojson_geometry(r.get(geom_col)) if geom_col else None
        geom_json = json.dumps(geom_obj) if geom_obj else None
        if geom_obj:
            geometry_rows += 1

        if (lat is None or lon is None) and geom_obj:
            g_lon, g_lat = _geometry_centroid_lon_lat(geom_obj)
            if lon is None:
                lon = g_lon
            if lat is None:
                lat = g_lat

        ts = None
        if ts_series is not None:
            v = ts_series.iloc[i]
            ts = None if pd.isna(v) else v.to_pydatetime()

        props: Dict[str, Any] = {}
        for k, v in r.items():
            if geom_col and k == geom_col and geom_obj is not None:
                props[k] = geom_obj
            else:
                props[k] = _safe_value(v)

        event_date_norm = ts.strftime("%Y-%m-%d") if ts else None
        event_time_norm = ts.strftime("%H:%M:%S") if ts else None
        if event_date_norm is None:
            event_date_norm = _normalize_date_text(r.get(date_col) if date_col else None)
        if event_date_norm is None and ts_source_col:
            event_date_norm = _normalize_date_text(r.get(ts_source_col))
        if event_time_norm is None:
            event_time_norm = _normalize_time_text(r.get(time_col) if time_col else None)
        if event_time_norm is None and ts_source_col:
            event_time_norm = _normalize_time_text(r.get(ts_source_col))
        if event_date_norm:
            props["_event_date_norm"] = event_date_norm
        if event_time_norm:
            props["_event_time_norm"] = event_time_norm

        rows.append(
            (
                dataset_id,
                uid,
                sid,
                uid,
                ts,
                lat,
                lon,
                json.dumps(props),
                geom_json,
                geom_json,
                geom_json,
                geom_json,
            )
        )

    with conn_factory() as conn, conn.cursor() as cur:
        ensure_core_tables_fn(cur)
        ensure_events_upload_columns_fn(cur)
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO """ + APP_EVENTS + """(
                dataset_id, owner_user_id, session_id, user_id, ts, lat, lon, props, geom_feature, geom_feature_m
            )
            VALUES %s
            """,
            rows,
            template="""
            (
                %s, %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) END,
                CASE WHEN %s IS NULL THEN NULL ELSE ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 26915) END
            )
            """,
            page_size=10_000,
        )

        owner_where, owner_val = _owner_clause(sid, uid)
        cur.execute(
            f"""
            UPDATE {APP_EVENTS}
            SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326),
                geom_m = ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 26915)
            WHERE dataset_id=%s AND {owner_where}
              AND geom IS NULL
              AND lat IS NOT NULL AND lon IS NOT NULL
            """,
            (dataset_id, owner_val),
        )

        cur.execute(f"SELECT COUNT(*) FROM {APP_EVENTS} WHERE dataset_id=%s AND {owner_where}", (dataset_id, owner_val))
        n = int(cur.fetchone()[0])

    mapping_fields = {
        "primary_id": next((c for c in ("hp_acc_image_no", "accident_id", "primary_id", "crash_id", "id") if c in df.columns), None),
        "timestamp": ts_col,
        "event_date": next((c for c in ("accident_date", "event_date", "crash_date", "date") if c in df.columns), None),
        "event_time": next((c for c in ("accident_time", "event_time", "crash_time", "time") if c in df.columns), None),
        "latitude": lat_col,
        "longitude": lon_col,
        "geometry": geom_col,
        "road_name": next((c for c in ("road", "road_name", "route", "street") if c in df.columns), None),
        "road_id": "road_segment_id",
        "way_id": "way_id",
    }

    column_profile_data = profile_columns(df)

    return {
        "rows_inserted": n,
        "lat_col": lat_col,
        "lon_col": lon_col,
        "ts_col": ts_col,
        "geom_col": geom_col,
        "geometry_rows": int(geometry_rows),
        "mapping_fields": mapping_fields,
        "column_profiles": column_profile_data.get("column_profiles", {}),
        "total_columns": column_profile_data.get("total_columns", len(df.columns)),
    }


def map_events_to_roads(
    dataset_id: str,
    max_dist_m: float,
    batch_size: int,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    ensure_core_tables_fn,
) -> Dict[str, Any]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    updated_total = 0

    with conn_factory() as conn, conn.cursor() as cur:
        ensure_core_tables_fn(cur)
        while True:
            cur.execute(
                f"""
                WITH todo AS (
                  SELECT e.id, e.geom_m
                  FROM {APP_EVENTS} e
                  WHERE e.dataset_id=%s AND e.{owner_where}
                    AND e.geom_m IS NOT NULL
                    AND e.road_segment_id IS NULL
                    AND EXISTS (
                      SELECT 1
                      FROM roads r
                      WHERE r.geom_m IS NOT NULL
                        AND ST_DWithin(r.geom_m, e.geom_m, %s)
                    )
                  LIMIT %s
                )
                UPDATE {APP_EVENTS} AS e
                SET road_segment_id = r.road_segment_id,
                    road_dist_m = r.dist_m,
                    road_conf = GREATEST(0.0, LEAST(1.0, 1.0 - (r.dist_m / %s)))
                FROM todo
                JOIN LATERAL (
                  SELECT
                    roads.road_segment_id,
                    ST_Distance(roads.geom_m, todo.geom_m) AS dist_m
                  FROM roads
                  WHERE roads.geom_m IS NOT NULL
                    AND ST_DWithin(roads.geom_m, todo.geom_m, %s)
                  ORDER BY roads.geom_m <-> todo.geom_m
                  LIMIT 1
                ) AS r ON TRUE
                WHERE e.id = todo.id
                RETURNING e.id
                """,
                (dataset_id, owner_val, max_dist_m, batch_size, max_dist_m, max_dist_m),
            )
            updated = cur.rowcount
            if updated == 0:
                break
            updated_total += updated
            conn.commit()

        cur.execute(
            f"""
            SELECT COUNT(*) AS total, COUNT(road_segment_id) AS matched
            FROM {APP_EVENTS}
            WHERE dataset_id=%s AND {owner_where}
            """,
            (dataset_id, owner_val),
        )
        total, matched = cur.fetchone()

        cur.execute(
            f"""
            UPDATE {APP_EVENTS} AS e
            SET props = e.props || jsonb_build_object('road', r.name, 'road_name', r.name)
            FROM roads r
            WHERE e.dataset_id=%s AND e.{owner_where}
              AND e.road_segment_id = r.road_segment_id
              AND r.name IS NOT NULL
              AND (e.props->>'road' IS NULL OR e.props->>'road' = '')
              AND (e.props->>'road_name' IS NULL OR e.props->>'road_name' = '')
              AND (e.props->>'roadName' IS NULL OR e.props->>'roadName' = '')
            """,
            (dataset_id, owner_val),
        )

    return {
        "total": int(total),
        "matched": int(matched),
        "updated_this_run": int(updated_total),
        "max_dist_m": float(max_dist_m),
    }


def map_events_to_osm_ways(
    dataset_id: str,
    max_dist_m: float,
    batch_size: int,
    overwrite_existing: bool,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    ensure_core_tables_fn,
    ensure_events_upload_columns_fn,
    logger_obj=None,
) -> Dict[str, Any]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    updated_total = 0
    t_start = time.perf_counter()
    batch_count = 0
    _logger = logger_obj or logger
    _logger.info(
        "upload.road_match.start dataset_id=%s session_id=%s max_dist_m=%s batch_size=%s overwrite_existing=%s",
        dataset_id,
        f"{uid}:{sid}",
        max_dist_m,
        batch_size,
        overwrite_existing,
    )

    with conn_factory() as conn, conn.cursor() as cur:
        ensure_core_tables_fn(cur)
        ensure_events_upload_columns_fn(cur)
        cur.execute("SELECT to_regclass('public.osm_roads_match')")
        rel = cur.fetchone()[0]
        if rel is None:
            raise ValueError("public.osm_roads_match does not exist. Load OSM roads first.")

        only_unmatched_sql = ""
        if not overwrite_existing:
            only_unmatched_sql = "AND e.road_segment_id IS NULL"

        while True:
            cur.execute(
                f"""
                WITH todo AS (
                  SELECT
                    e.id,
                    COALESCE(e.geom_feature, e.geom) AS match_geom
                  FROM {APP_EVENTS} e
                  WHERE e.dataset_id=%s
                    AND e.{owner_where}
                    AND COALESCE(e.geom_feature, e.geom) IS NOT NULL
                    {only_unmatched_sql}
                  LIMIT %s
                ),
                matched AS (
                  SELECT
                    todo.id,
                    r.way_id,
                    r.ref,
                    r.label,
                    r.name,
                    r.highway,
                    ST_Distance(r.geom_3857, ST_Transform(todo.match_geom, 3857)) AS dist_m
                  FROM todo
                  JOIN LATERAL (
                    SELECT
                      way_id, ref, label, name, highway, geom_3857
                    FROM public.osm_roads_match
                    WHERE geom_3857 IS NOT NULL
                      AND ST_DWithin(geom_3857, ST_Transform(todo.match_geom, 3857), %s)
                    ORDER BY geom_3857 <-> ST_Transform(todo.match_geom, 3857)
                    LIMIT 1
                  ) r ON TRUE
                )
                UPDATE {APP_EVENTS} AS e
                SET
                  way_id = m.way_id,
                  road_segment_id = m.way_id::text,
                  road_dist_m = m.dist_m,
                  road_conf = GREATEST(0.0, LEAST(1.0, 1.0 - (m.dist_m / %s))),
                  props = (
                    e.props
                    || jsonb_build_object('way_id', m.way_id)
                    || CASE
                         WHEN COALESCE(NULLIF(e.props->>'road', ''), NULLIF(e.props->>'road_name', ''), NULLIF(e.props->>'roadName', '')) IS NULL
                         THEN jsonb_strip_nulls(
                                jsonb_build_object(
                                  'road', COALESCE(NULLIF(m.label, ''), NULLIF(m.ref, ''), NULLIF(m.name, '')),
                                  'road_name', COALESCE(NULLIF(m.label, ''), NULLIF(m.ref, ''), NULLIF(m.name, ''))
                                )
                              )
                         ELSE '{{}}'::jsonb
                       END
                    || jsonb_strip_nulls(
                         jsonb_build_object(
                           'road_ref', NULLIF(m.ref, ''),
                           'road_highway', NULLIF(m.highway, '')
                         )
                       )
                  )
                FROM matched m
                WHERE e.id = m.id
                RETURNING e.id
                """,
                (dataset_id, owner_val, batch_size, max_dist_m, max_dist_m),
            )
            updated = cur.rowcount
            if updated == 0:
                break
            batch_count += 1
            updated_total += updated
            conn.commit()
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)
            _logger.info(
                "upload.road_match.batch dataset_id=%s batch=%s updated=%s updated_total=%s elapsed_ms=%s",
                dataset_id,
                batch_count,
                updated,
                updated_total,
                elapsed_ms,
            )

        cur.execute(
            f"""
            SELECT COUNT(*) AS total, COUNT(road_segment_id) AS matched
            FROM {APP_EVENTS}
            WHERE dataset_id=%s AND {owner_where}
            """,
            (dataset_id, owner_val),
        )
        total, matched = cur.fetchone()

    elapsed_ms = int((time.perf_counter() - t_start) * 1000)
    _logger.info(
        "upload.road_match.complete dataset_id=%s batches=%s updated_total=%s matched=%s total=%s elapsed_ms=%s",
        dataset_id,
        batch_count,
        updated_total,
        int(matched),
        int(total),
        elapsed_ms,
    )
    return {
        "total": int(total),
        "matched": int(matched),
        "updated_this_run": int(updated_total),
        "max_dist_m": float(max_dist_m),
        "road_segment_id_column": "road_segment_id",
        "way_id_column": "way_id",
        "source": "public.osm_roads_match",
    }

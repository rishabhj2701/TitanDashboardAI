from __future__ import annotations

import logging
import os
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException

from dynamic_analyst import postgis_store
from dynamic_analyst.session_state import set_active_session
from dynamic_analyst.storage.postgis.table_names import APP_DATASETS, APP_EVENTS

router = APIRouter()
logger = logging.getLogger("adk_server")

SPEED_EXPR = "COALESCE(NULLIF(attrs->>'speed','')::float8, NULLIF(attrs->>'SpeedMPH','')::float8)"
SPEED_LIMIT_EXPR = (
    "COALESCE(NULLIF(attrs->>'speed_limit','')::float8, "
    "NULLIF(attrs->>'speed_limit_mph','')::float8, "
    "NULLIF(attrs->>'SpeedLimitMPH','')::float8)"
)


def _strict_data_quality_errors() -> bool:
    return os.environ.get("DATA_QUALITY_STRICT_ERRORS", "0").strip().lower() in {"1", "true", "yes", "on"}


def _data_quality_error_response(endpoint: str, default_payload: dict, exc: Exception) -> dict:
    logger.error("%s failed: %s", endpoint, exc, exc_info=True)
    if _strict_data_quality_errors():
        raise HTTPException(status_code=500, detail=f"{endpoint} failed.")
    return default_payload


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        return "anonymous"
    set_active_session(x_session_id)
    return x_session_id


@router.get("/api/cv/data-quality")
def cv_data_quality(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM cv_points")
            total_rows = (cur.fetchone() or {}).get("total", 0)

            if total_rows == 0:
                return {"status": "success", "total_rows": 0, "fields": {}, "overall_completeness": 0}

            fields = {}
            for col in ("ts", "lat", "lon", "road_segment_id", "road_dist_m", "road_conf"):
                cur.execute(
                    f"SELECT COUNT({col}) AS non_null, COUNT(*) - COUNT({col}) AS null_count FROM cv_points"
                )
                row = cur.fetchone() or {}
                non_null = row.get("non_null", 0)
                null_count = row.get("null_count", 0)
                completeness = round(non_null / total_rows, 4) if total_rows else 0
                fields[col] = {"non_null": non_null, "null_count": null_count, "completeness": completeness}

            # Check attrs JSON keys on a sample
            cur.execute(
                "SELECT DISTINCT jsonb_object_keys(attrs) AS k FROM cv_points TABLESAMPLE SYSTEM(1) LIMIT 50"
            )
            attr_keys = [r["k"] for r in cur.fetchall()]
            for key in attr_keys:
                cur.execute(
                    "SELECT COUNT(*) FILTER (WHERE attrs->>%s IS NOT NULL) AS non_null, "
                    "COUNT(*) FILTER (WHERE attrs->>%s IS NULL) AS null_count FROM cv_points",
                    (key, key),
                )
                row = cur.fetchone() or {}
                non_null = row.get("non_null", 0)
                null_count = row.get("null_count", 0)
                completeness = round(non_null / total_rows, 4) if total_rows else 0
                fields[f"attrs.{key}"] = {"non_null": non_null, "null_count": null_count, "completeness": completeness}

            all_completeness = [f["completeness"] for f in fields.values()]
            overall = round(sum(all_completeness) / len(all_completeness), 4) if all_completeness else 0

            return {"status": "success", "total_rows": total_rows, "fields": fields, "overall_completeness": overall}
    except Exception as exc:
        return _data_quality_error_response(
            "cv data quality",
            {"status": "error", "total_rows": 0, "fields": {}, "overall_completeness": 0, "error": str(exc)},
            exc,
        )


@router.get("/api/cv/summary-stats")
def cv_summary_stats(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM cv_points")
            cv_count = (cur.fetchone() or {}).get("total", 0)

            avg_speed = 0
            max_speed = 0
            if cv_count > 0:
                cur.execute(
                    "SELECT "
                    "AVG(CASE "
                    "  WHEN btrim(COALESCE(attrs->>'speed', '')) ~ '^[+-]?([0-9]+(\\.[0-9]+)?|\\.[0-9]+)$' "
                    "  THEN btrim(attrs->>'speed')::float8 "
                    "END) AS avg_speed, "
                    "MAX(CASE "
                    "  WHEN btrim(COALESCE(attrs->>'speed', '')) ~ '^[+-]?([0-9]+(\\.[0-9]+)?|\\.[0-9]+)$' "
                    "  THEN btrim(attrs->>'speed')::float8 "
                    "END) AS max_speed "
                    "FROM cv_points"
                )
                row = cur.fetchone() or {}
                avg_speed = round(row.get("avg_speed") or 0, 1)
                max_speed = round(row.get("max_speed") or 0, 1)

            cur.execute(
                "SELECT COUNT(*) AS total "
                f"FROM {APP_EVENTS} e "
                f"JOIN {APP_DATASETS} d ON d.dataset_id = e.dataset_id "
                "WHERE LOWER(COALESCE(d.entity_type, '')) = 'crash'"
            )
            crash_count = (cur.fetchone() or {}).get("total", 0)

            hard_braking = 0
            cur.execute("SELECT to_regclass('public.cv_hard_brake') AS rel")
            if (cur.fetchone() or {}).get("rel"):
                cur.execute("SELECT COUNT(*) AS total FROM cv_hard_brake")
                hard_braking = (cur.fetchone() or {}).get("total", 0)

            return {
                "status": "success",
                "total": cv_count + crash_count + hard_braking,
                "cvPoints": cv_count,
                "crashes": crash_count,
                "hardBraking": hard_braking,
                "avgSpeed": avg_speed,
                "maxSpeed": max_speed,
            }
    except Exception as exc:
        return _data_quality_error_response(
            "cv summary stats",
            {"status": "error", "total": 0, "cvPoints": 0, "crashes": 0, "hardBraking": 0, "avgSpeed": 0, "maxSpeed": 0},
            exc,
        )


@router.get("/api/cv/speed-compliance")
def cv_speed_compliance(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE {SPEED_EXPR} IS NOT NULL
                          AND {SPEED_LIMIT_EXPR} IS NOT NULL
                          AND {SPEED_EXPR} <= {SPEED_LIMIT_EXPR}
                    ) AS within_limit,
                    COUNT(*) FILTER (
                        WHERE {SPEED_EXPR} IS NOT NULL
                          AND {SPEED_LIMIT_EXPR} IS NOT NULL
                          AND {SPEED_EXPR} > {SPEED_LIMIT_EXPR}
                    ) AS over_limit,
                    COUNT(*) FILTER (
                        WHERE {SPEED_EXPR} IS NULL OR {SPEED_LIMIT_EXPR} IS NULL
                    ) AS no_limit_data
                FROM cv_points
            """)
            row = cur.fetchone() or {}
            total = row.get("total", 0)
            within = row.get("within_limit", 0)
            over = row.get("over_limit", 0)
            no_data = row.get("no_limit_data", 0)
            return {
                "status": "success",
                "total": total,
                "within_limit": within,
                "over_limit": over,
                "no_limit_data": no_data,
                "within_pct": round(within / total * 100, 1) if total else 0,
                "over_pct": round(over / total * 100, 1) if total else 0,
            }
    except Exception as exc:
        return _data_quality_error_response(
            "speed compliance",
            {"status": "error", "total": 0, "within_limit": 0, "over_limit": 0, "no_limit_data": 0, "within_pct": 0, "over_pct": 0},
            exc,
        )


@router.get("/api/cv/top-speeding-roads")
def cv_top_speeding_roads(
    limit: int = 10,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.cv_road_stats_mv') AS rel")
            has_mv = (cur.fetchone() or {}).get("rel")
            if has_mv:
                cur.execute("""
                    SELECT COALESCE(label, name, ref, 'Way ' || way_id) AS road_name,
                           avg_speed_mph, speed_limit_mph,
                           ROUND((avg_speed_mph - speed_limit_mph)::numeric, 1) AS speed_over_limit,
                           point_count
                    FROM cv_road_stats_mv
                    WHERE avg_speed_mph IS NOT NULL AND speed_limit_mph IS NOT NULL
                      AND avg_speed_mph > speed_limit_mph
                    ORDER BY speed_over_limit DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute(f"""
                    SELECT attrs->>'original_road' AS road_name,
                           ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed_mph,
                           ROUND(AVG({SPEED_LIMIT_EXPR})::numeric, 1) AS speed_limit_mph,
                           ROUND((AVG({SPEED_EXPR}) - AVG({SPEED_LIMIT_EXPR}))::numeric, 1) AS speed_over_limit,
                           COUNT(*) AS point_count
                    FROM cv_points
                    WHERE {SPEED_EXPR} IS NOT NULL AND {SPEED_LIMIT_EXPR} IS NOT NULL
                    GROUP BY attrs->>'original_road'
                    HAVING AVG({SPEED_EXPR}) > AVG({SPEED_LIMIT_EXPR})
                    ORDER BY speed_over_limit DESC
                    LIMIT %s
                """, (limit,))
            roads = cur.fetchall()
            return {"status": "success", "roads": roads}
    except Exception as exc:
        return _data_quality_error_response("top speeding roads", {"status": "error", "roads": []}, exc)


@router.get("/api/cv/hourly-trend")
def cv_hourly_trend(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT EXTRACT(HOUR FROM ts)::int AS hour,
                       ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed,
                       COUNT(*) AS point_count
                FROM cv_points
                WHERE ts IS NOT NULL AND {SPEED_EXPR} IS NOT NULL
                GROUP BY EXTRACT(HOUR FROM ts)::int
                ORDER BY hour
            """)
            hours = cur.fetchall()
            return {"status": "success", "hours": hours}
    except Exception as exc:
        return _data_quality_error_response("hourly trend", {"status": "error", "hours": []}, exc)


@router.get("/api/cv/county-breakdown")
def cv_county_breakdown(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT COALESCE(attrs->>'county', 'Unknown') AS county,
                       COUNT(*) AS point_count,
                       ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed
                FROM cv_points
                GROUP BY COALESCE(attrs->>'county', 'Unknown')
                ORDER BY point_count DESC
            """)
            counties = cur.fetchall()
            return {"status": "success", "counties": counties}
    except Exception as exc:
        return _data_quality_error_response("county breakdown", {"status": "error", "counties": []}, exc)


@router.get("/api/cv/func-class-stats")
def cv_func_class_stats(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT COALESCE(attrs->>'func_class', 'Unknown') AS func_class,
                       COUNT(*) AS point_count,
                       ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed,
                       ROUND(AVG({SPEED_LIMIT_EXPR})::numeric, 1) AS avg_speed_limit
                FROM cv_points
                GROUP BY COALESCE(attrs->>'func_class', 'Unknown')
                ORDER BY point_count DESC
            """)
            classes = cur.fetchall()
            return {"status": "success", "classes": classes}
    except Exception as exc:
        return _data_quality_error_response("func class stats", {"status": "error", "classes": []}, exc)


@router.get("/api/cv/speed-distribution")
def cv_speed_distribution(
    bucket_size: int = 10,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT (FLOOR({SPEED_EXPR} / %s) * %s)::int AS bucket_min,
                       (FLOOR({SPEED_EXPR} / %s) * %s + %s)::int AS bucket_max,
                       COUNT(*) AS point_count
                FROM cv_points
                WHERE {SPEED_EXPR} IS NOT NULL
                GROUP BY FLOOR({SPEED_EXPR} / %s)
                ORDER BY bucket_min
            """, (bucket_size, bucket_size, bucket_size, bucket_size, bucket_size, bucket_size))
            buckets = cur.fetchall()
            return {"status": "success", "buckets": buckets, "bucket_size": bucket_size}
    except Exception as exc:
        return _data_quality_error_response(
            "speed distribution",
            {"status": "error", "buckets": [], "bucket_size": bucket_size},
            exc,
        )


@router.get("/api/cv/top-roads-volume")
def cv_top_roads_volume(
    limit: int = 15,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.cv_road_stats_mv') AS rel")
            has_mv = (cur.fetchone() or {}).get("rel")
            if has_mv:
                cur.execute("""
                    SELECT COALESCE(label, name, ref, 'Way ' || way_id) AS road_name,
                           point_count,
                           ROUND(avg_speed_mph::numeric, 1) AS avg_speed_mph
                    FROM cv_road_stats_mv
                    WHERE point_count IS NOT NULL
                    ORDER BY point_count DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute(f"""
                    SELECT COALESCE(attrs->>'original_road', 'Unknown') AS road_name,
                           COUNT(*) AS point_count,
                           ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed_mph
                    FROM cv_points
                    GROUP BY COALESCE(attrs->>'original_road', 'Unknown')
                    ORDER BY point_count DESC
                    LIMIT %s
                """, (limit,))
            roads = cur.fetchall()
            return {"status": "success", "roads": roads}
    except Exception as exc:
        return _data_quality_error_response("top roads volume", {"status": "error", "roads": []}, exc)


@router.get("/api/cv/day-of-week-trend")
def cv_day_of_week_trend(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT EXTRACT(ISODOW FROM ts)::int AS dow,
                       ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed,
                       COUNT(*) AS point_count
                FROM cv_points
                WHERE ts IS NOT NULL AND {SPEED_EXPR} IS NOT NULL
                GROUP BY EXTRACT(ISODOW FROM ts)::int
                ORDER BY dow
            """)
            days = cur.fetchall()
            return {"status": "success", "days": days}
    except Exception as exc:
        return _data_quality_error_response("day of week trend", {"status": "error", "days": []}, exc)


@router.get("/api/cv/speed-vs-limit")
def cv_speed_vs_limit(
    limit: int = 20,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.cv_road_stats_mv') AS rel")
            has_mv = (cur.fetchone() or {}).get("rel")
            if has_mv:
                cur.execute("""
                    SELECT COALESCE(label, name, ref, 'Way ' || way_id) AS road_name,
                           ROUND(avg_speed_mph::numeric, 1) AS avg_speed_mph,
                           ROUND(speed_limit_mph::numeric, 1) AS speed_limit_mph,
                           point_count
                    FROM cv_road_stats_mv
                    WHERE avg_speed_mph IS NOT NULL AND speed_limit_mph IS NOT NULL
                      AND point_count >= 5
                    ORDER BY point_count DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute(f"""
                    SELECT COALESCE(attrs->>'original_road', 'Unknown') AS road_name,
                           ROUND(AVG({SPEED_EXPR})::numeric, 1) AS avg_speed_mph,
                           ROUND(AVG({SPEED_LIMIT_EXPR})::numeric, 1) AS speed_limit_mph,
                           COUNT(*) AS point_count
                    FROM cv_points
                    WHERE {SPEED_EXPR} IS NOT NULL AND {SPEED_LIMIT_EXPR} IS NOT NULL
                    GROUP BY COALESCE(attrs->>'original_road', 'Unknown')
                    HAVING COUNT(*) >= 5
                    ORDER BY point_count DESC
                    LIMIT %s
                """, (limit,))
            roads = cur.fetchall()
            return {"status": "success", "roads": roads}
    except Exception as exc:
        return _data_quality_error_response("speed vs limit", {"status": "error", "roads": []}, exc)


@router.get("/api/cv/hourly-vehicle-counts")
def cv_hourly_vehicle_counts(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)
    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Find cv_road_stats_mv — check per-run schemas via cv_runs first, then public
            mv_table = None
            cur.execute("SELECT to_regclass('public.cv_runs') AS rel")
            if (cur.fetchone() or {}).get("rel"):
                cur.execute("SELECT schema_name FROM public.cv_runs ORDER BY run_id DESC")
                for row in cur.fetchall():
                    schema = (row.get("schema_name") or "").strip()
                    if schema:
                        candidate = f"{schema}.cv_road_stats_mv"
                        cur.execute("SELECT to_regclass(%s)", (candidate,))
                        if (cur.fetchone() or {}).get("to_regclass"):
                            mv_table = candidate
                            break
            if not mv_table:
                cur.execute("SELECT to_regclass('public.cv_road_stats_mv') AS rel")
                if (cur.fetchone() or {}).get("rel"):
                    mv_table = "public.cv_road_stats_mv"

            if mv_table:
                # Check if hourly_unique_vehicles_json column exists
                cur.execute("""
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = to_regclass(%s)
                      AND attname = 'hourly_unique_vehicles_json'
                      AND attnum > 0 AND NOT attisdropped
                    LIMIT 1
                """, (mv_table,))
                has_hourly_json = cur.fetchone() is not None
            else:
                has_hourly_json = False

            if mv_table and has_hourly_json:
                cur.execute(f"""
                    SELECT
                        h.hour::int AS hour,
                        ROUND(SUM(h.avg_vehicles)::numeric, 1) AS total_vehicles
                    FROM {mv_table},
                    LATERAL (
                        SELECT key AS hour, value::text::float8 AS avg_vehicles
                        FROM jsonb_each_text(hourly_unique_vehicles_json)
                    ) h
                    WHERE hourly_unique_vehicles_json IS NOT NULL
                    GROUP BY h.hour::int
                    ORDER BY h.hour::int
                """)
                hours = cur.fetchall()

                cur.execute(f"""
                    SELECT COALESCE(SUM(unique_vehicles_total), 0) AS total_unique_vehicles
                    FROM {mv_table}
                    WHERE unique_vehicles_total IS NOT NULL
                """)
                summary = cur.fetchone() or {}

                return {
                    "status": "success",
                    "hours": hours,
                    "total_unique_vehicles": summary.get("total_unique_vehicles", 0),
                }

            # Fallback: count from cv_points by hour
            cur.execute("SELECT to_regclass('public.cv_points') AS rel")
            if not (cur.fetchone() or {}).get("rel"):
                return {"status": "success", "hours": [], "total_unique_vehicles": 0}
            cur.execute("""
                SELECT EXTRACT(HOUR FROM ts)::int AS hour,
                       COUNT(DISTINCT vehicle_id) AS total_vehicles
                FROM cv_points
                WHERE ts IS NOT NULL AND vehicle_id IS NOT NULL AND vehicle_id != ''
                GROUP BY EXTRACT(HOUR FROM ts)::int
                ORDER BY hour
            """)
            hours = cur.fetchall()
            return {
                "status": "success",
                "hours": hours,
                "total_unique_vehicles": 0,
            }
    except Exception as exc:
        return _data_quality_error_response(
            "hourly vehicle counts",
            {"status": "error", "hours": [], "total_unique_vehicles": 0},
            exc,
        )

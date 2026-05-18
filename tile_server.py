import logging
import os
import threading
import time
import json
import hashlib
import re
import uuid
from typing import Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from dynamic_analyst import postgis_store
from dynamic_analyst.storage.postgis import db_pool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("adk_tile_server")
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
PRODUCTION_ENVS = {"production", "prod"}
REQUEST_TIMING_LOG_ENABLED = os.environ.get("REQUEST_TIMING_LOG_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_LOG_HEALTH = os.environ.get("REQUEST_TIMING_LOG_HEALTH", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_INCLUDE_QUERY = os.environ.get("REQUEST_TIMING_INCLUDE_QUERY", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_ADD_HEADER = os.environ.get("REQUEST_TIMING_ADD_HEADER", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_SLOW_MS = float(os.environ.get("REQUEST_TIMING_SLOW_MS", "1500"))
DB_HEALTH_STMT_TIMEOUT_MS = int(os.environ.get("DB_HEALTH_STMT_TIMEOUT_MS", "1500"))
DB_HEALTH_CACHE_TTL_S = max(0.0, float(os.environ.get("DB_HEALTH_CACHE_TTL_S", "3.0")))

def validate_production_config() -> None:
    if APP_ENV not in PRODUCTION_ENVS:
        return
    issues: list[str] = []
    if not os.environ.get("POSTGIS_DSN", "").strip():
        issues.append("POSTGIS_DSN must be configured.")
    if REQUEST_TIMING_INCLUDE_QUERY:
        issues.append("REQUEST_TIMING_INCLUDE_QUERY must be disabled in production.")
    if issues:
        raise RuntimeError(
            "Invalid production configuration:\n- " + "\n- ".join(issues)
        )


validate_production_config()

app = FastAPI(title="Google ADK Tile Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _should_log_request_timing(path: str) -> bool:
    if path.startswith("/tiles/") or path.startswith("/api/"):
        return True
    if path in {"/healthz", "/health/db"}:
        return REQUEST_TIMING_LOG_HEALTH
    return False


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    path = request.url.path or "/"
    if not REQUEST_TIMING_LOG_ENABLED or not _should_log_request_timing(path):
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    response = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if response is not None and REQUEST_TIMING_ADD_HEADER:
            response.headers["X-Request-Duration-Ms"] = f"{elapsed_ms:.1f}"
        uri = path
        query = request.url.query
        if REQUEST_TIMING_INCLUDE_QUERY and query:
            uri = f"{path}?{query}"
        client_ip = request.client.host if request.client else "-"
        log_level = logging.WARNING if status_code >= 500 or elapsed_ms >= REQUEST_TIMING_SLOW_MS else logging.INFO
        logger.log(
            log_level,
            "http.request method=%s path=%s status=%s duration_ms=%.1f client=%s",
            request.method,
            uri,
            status_code,
            elapsed_ms,
            client_ip,
        )

_ROADS_BBOX_CACHE: Dict[str, tuple[float, dict]] = {}
_ROAD_TILE_CACHE: Dict[str, tuple[float, bytes]] = {}
_ROAD_TILE_CACHE_TTL_S = 180.0
_ROAD_TILE_CACHE_MAX_ENTRIES = 128
_ROAD_TILE_CACHE_MAX_TILE_BYTES = 12_000_000
_TILE_CACHE_CONTROL = "public, max-age=600, s-maxage=1800, stale-while-revalidate=120, stale-if-error=86400"
_EMPTY_TILE_CACHE_CONTROL = "public, max-age=60, s-maxage=300, stale-while-revalidate=30, stale-if-error=300"
_FAILURE_TILE_CACHE_CONTROL = "no-store, max-age=0"
_BBOX_CACHE_CONTROL = "public, max-age=30, s-maxage=120, stale-while-revalidate=60, stale-if-error=300"
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DB_HEALTH_CACHE_LOCK = threading.Lock()
_DB_HEALTH_CACHE: tuple[float, int, dict] | None = None


def _latest_cv_dataset_id() -> Optional[str]:
    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_id
                FROM datasets
                WHERE status = 'ready'
                  AND entity_type = 'cv'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass

    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT dataset_id FROM cv_points ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _safe_schema(schema_name: Optional[str]) -> Optional[str]:
    schema = (schema_name or "").strip()
    if not schema:
        return None
    if not _SAFE_IDENT_RE.match(schema):
        return None
    return schema


def _relation_exists(cur, relation_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (relation_name,))
    row = cur.fetchone()
    return bool(row and row[0])


def _schema_for_run_id(cur, run_id: Optional[str]) -> Optional[str]:
    rid = (run_id or "").strip()
    if not rid or rid == "__all__":
        return None
    try:
        cur.execute("SELECT to_regclass('public.cv_runs') AS rel")
        if not (cur.fetchone() or (None,))[0]:
            return None
        cur.execute("SELECT schema_name FROM public.cv_runs WHERE run_id = %s LIMIT 1", (rid,))
        row = cur.fetchone()
        schema_name = _safe_schema(row[0] if row else None)
        return schema_name
    except Exception:
        try:
            cur.execute("SAVEPOINT _sr; ROLLBACK TO SAVEPOINT _sr")
        except Exception:
            pass
        return None


def _road_source_candidates(cur, dataset_id: Optional[str]) -> list[str]:
    candidates: list[str] = []
    schema_name = _schema_for_run_id(cur, dataset_id)
    if schema_name:
        candidates.extend(
            [
                f"{schema_name}.cv_road_stats_mv",
                f"{schema_name}.cv_road_segment_stats",
                f"{schema_name}.viz_matched_roads_tbl",
                f"{schema_name}.roads",
            ]
        )
    candidates.extend(
        [
            "public.cv_road_stats_mv",
            "public.viz_matched_roads_tbl",
            "mm_rawmatch.cv_road_segment_stats",
            "public.cv_road_segment_stats",
            "public.roads",
        ]
    )
    deduped: list[str] = []
    seen: set[str] = set()
    for relation in candidates:
        if relation in seen:
            continue
        seen.add(relation)
        deduped.append(relation)
    return [relation for relation in deduped if _relation_exists(cur, relation)]


def _weak_etag(data: bytes) -> str:
    digest = hashlib.blake2s(data, digest_size=8).hexdigest()
    return f'W/"{digest}"'


def _etag_matches(request: Request, etag: str) -> bool:
    incoming = request.headers.get("if-none-match")
    if not incoming:
        return False
    tokens = [part.strip() for part in incoming.split(",") if part.strip()]
    if "*" in tokens:
        return True
    return etag in tokens


def _request_id_for(request: Request) -> str:
    incoming = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or request.headers.get("x-amzn-trace-id")
    )
    if incoming:
        return incoming.strip()[:80]
    return uuid.uuid4().hex[:16]


def _tile_error_code(error: Exception) -> str:
    raw = f"{type(error).__name__}: {error}"
    normalized = raw.lower()
    if "connection pool exhausted" in normalized:
        return "pool_exhausted"
    if "too many clients" in normalized:
        return "db_too_many_clients"
    if "timeout" in normalized or "timed out" in normalized:
        return "db_timeout"
    return type(error).__name__


def _normalize_road_tile_mode(mode: Optional[str]) -> str:
    raw = (mode or "").strip().lower()
    if raw in {"vehicle", "vehicles", "vehicle-count"}:
        return "vehicles"
    return "speed"


def _log_tile_event(level: int, event: str, **fields) -> None:
    payload = {"event": event, **fields}
    logger.log(level, "tile.road %s", json.dumps(payload, sort_keys=True, default=str))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _build_db_health_payload() -> tuple[dict, int]:
    db = db_pool.ping_db(DB_HEALTH_STMT_TIMEOUT_MS)
    pool = db_pool.get_pool_stats()
    pressure = "high" if pool["utilization"] >= 0.9 else ("medium" if pool["utilization"] >= 0.7 else "low")
    payload = {
        "status": "ok" if db["ok"] else "degraded",
        "db": db,
        "pool": {
            **pool,
            "pressure": pressure,
        },
    }
    return payload, 200 if db["ok"] else 503


def _db_health(force_refresh: bool = False) -> tuple[dict, int]:
    global _DB_HEALTH_CACHE
    now = time.perf_counter()
    if not force_refresh and DB_HEALTH_CACHE_TTL_S > 0:
        with _DB_HEALTH_CACHE_LOCK:
            cached = _DB_HEALTH_CACHE
        if cached and (now - cached[0]) <= DB_HEALTH_CACHE_TTL_S:
            payload = dict(cached[2])
            payload["cached"] = True
            payload["cache_age_ms"] = round((now - cached[0]) * 1000.0, 1)
            payload["cache_ttl_s"] = DB_HEALTH_CACHE_TTL_S
            return payload, cached[1]

    payload, status_code = _build_db_health_payload()
    payload["cached"] = False
    payload["cache_age_ms"] = 0.0
    payload["cache_ttl_s"] = DB_HEALTH_CACHE_TTL_S
    with _DB_HEALTH_CACHE_LOCK:
        _DB_HEALTH_CACHE = (now, status_code, dict(payload))
    return payload, status_code


@app.get("/health/db")
def health_db():
    payload, status_code = _db_health(force_refresh=False)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/api/health/db")
def api_health_db():
    payload, status_code = _db_health(force_refresh=False)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/api/health/db/live")
def api_health_db_live():
    payload, status_code = _db_health(force_refresh=True)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/api/roads/bbox")
def roads_bbox(request: Request, dataset_id: Optional[str] = None):
    t0 = time.perf_counter()
    cache_key = dataset_id or "__default__"
    cached = _ROADS_BBOX_CACHE.get(cache_key)
    if cached and (time.perf_counter() - cached[0]) < 300:
        payload = cached[1]
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        etag = _weak_etag(payload_bytes)
        headers = {"Cache-Control": _BBOX_CACHE_CONTROL, "ETag": etag}
        if _etag_matches(request, etag):
            return Response(status_code=304, headers=headers)
        return Response(content=payload_bytes, media_type="application/json", headers=headers)
    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            def _has_column(table: str, column: str) -> bool:
                # Use pg_attribute so materialized views are handled correctly.
                cur.execute(
                    """
                    SELECT 1
                    FROM pg_attribute
                    WHERE attrelid = to_regclass(%s)
                      AND attname = %s
                      AND attnum > 0
                      AND NOT attisdropped
                    LIMIT 1
                    """,
                    (table, column),
                )
                return cur.fetchone() is not None

            candidates = _road_source_candidates(cur, dataset_id)
            table = candidates[0] if candidates else None
            if not table:
                logger.info("roads_bbox: no table found (%.1f ms)", (time.perf_counter() - t0) * 1000)
                payload = {"bbox": None}
                payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                etag = _weak_etag(payload_bytes)
                headers = {"Cache-Control": _BBOX_CACHE_CONTROL, "ETag": etag}
                if _etag_matches(request, etag):
                    return Response(status_code=304, headers=headers)
                return Response(content=payload_bytes, media_type="application/json", headers=headers)

            has_geom_3857 = _has_column(table, "geom_3857")
            geom_col = "geom_3857" if has_geom_3857 else "geom"
            geom_expr = f"ST_Transform({geom_col}, 4326)"

            where_parts = [f"{geom_col} IS NOT NULL"]
            params: list = []
            if dataset_id and dataset_id != "__all__" and _has_column(table, "dataset_id"):
                where_parts.append("dataset_id = %s")
                params.append(dataset_id)

            sql = f"""
                SELECT ST_XMin(ext), ST_YMin(ext), ST_XMax(ext), ST_YMax(ext)
                FROM (
                  SELECT ST_Extent({geom_expr}) AS ext
                  FROM {table}
                  WHERE {" AND ".join(where_parts)}
                ) s
            """
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row or any(v is None for v in row):
                logger.info(
                    "roads_bbox: empty bbox table=%s dataset_id=%s (%.1f ms)",
                    table,
                    dataset_id,
                    (time.perf_counter() - t0) * 1000,
                )
                payload = {"bbox": None}
                payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                etag = _weak_etag(payload_bytes)
                headers = {"Cache-Control": _BBOX_CACHE_CONTROL, "ETag": etag}
                if _etag_matches(request, etag):
                    return Response(status_code=304, headers=headers)
                return Response(content=payload_bytes, media_type="application/json", headers=headers)
            minx, miny, maxx, maxy = row
            payload = {"bbox": {"minLon": minx, "minLat": miny, "maxLon": maxx, "maxLat": maxy}}
            _ROADS_BBOX_CACHE[cache_key] = (time.perf_counter(), payload)
            logger.info(
                "roads_bbox: table=%s dataset_id=%s (%.1f ms)",
                table,
                dataset_id,
                (time.perf_counter() - t0) * 1000,
            )
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            etag = _weak_etag(payload_bytes)
            headers = {"Cache-Control": _BBOX_CACHE_CONTROL, "ETag": etag}
            if _etag_matches(request, etag):
                return Response(status_code=304, headers=headers)
            return Response(content=payload_bytes, media_type="application/json", headers=headers)
    except Exception as e:
        logger.warning(f"roads_bbox failed: {e}")
        payload = {"bbox": None}
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        etag = _weak_etag(payload_bytes)
        headers = {"Cache-Control": _BBOX_CACHE_CONTROL, "ETag": etag}
        if _etag_matches(request, etag):
            return Response(status_code=304, headers=headers)
        return Response(content=payload_bytes, media_type="application/json", headers=headers)


def _road_tile_cache_key(dataset_id: str, mode: str, z: int, x: int, y: int, min_points: int = 20) -> str:
    return f"{dataset_id}:{mode}:{min_points}:{z}:{x}:{y}"


def _road_tile_cache_get(dataset_id: str, mode: str, z: int, x: int, y: int, min_points: int = 20) -> Optional[bytes]:
    key = _road_tile_cache_key(dataset_id, mode, z, x, y, min_points)
    item = _ROAD_TILE_CACHE.get(key)
    if not item:
        return None
    ts, tile = item
    now = time.perf_counter()
    if now - ts > _ROAD_TILE_CACHE_TTL_S:
        _ROAD_TILE_CACHE.pop(key, None)
        return None
    _ROAD_TILE_CACHE.pop(key, None)
    _ROAD_TILE_CACHE[key] = (now, tile)
    return tile


def _road_tile_cache_put(dataset_id: str, mode: str, z: int, x: int, y: int, tile: bytes, min_points: int = 20) -> None:
    if len(tile) > _ROAD_TILE_CACHE_MAX_TILE_BYTES:
        return
    key = _road_tile_cache_key(dataset_id, mode, z, x, y, min_points)
    now = time.perf_counter()
    _ROAD_TILE_CACHE[key] = (now, tile)

    expired = [k for k, (ts, _) in _ROAD_TILE_CACHE.items() if now - ts > _ROAD_TILE_CACHE_TTL_S]
    for k in expired:
        _ROAD_TILE_CACHE.pop(k, None)

    while len(_ROAD_TILE_CACHE) > _ROAD_TILE_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(_ROAD_TILE_CACHE))
        _ROAD_TILE_CACHE.pop(oldest_key, None)


@app.get("/tiles/roads/{z}/{x}/{y}.mvt")
def road_segment_tiles(
    request: Request,
    z: int,
    x: int,
    y: int,
    dataset_id: Optional[str] = None,
    min_points: int = 20,
    mode: str = "speed",
):
    request_id = _request_id_for(request)
    started = time.perf_counter()
    dataset_id = dataset_id or os.environ.get("DEFAULT_CV_DATASET") or _latest_cv_dataset_id() or "__all__"
    min_points = max(0, int(min_points))
    road_tile_mode = _normalize_road_tile_mode(mode)

    cached_tile = _road_tile_cache_get(dataset_id, road_tile_mode, z, x, y, min_points)
    if cached_tile is not None:
        etag = _weak_etag(cached_tile)
        cache_control = _TILE_CACHE_CONTROL if cached_tile else _EMPTY_TILE_CACHE_CONTROL
        headers = {
            "Cache-Control": cache_control,
            "ETag": etag,
            "X-Request-ID": request_id,
            "X-Road-Mode": road_tile_mode,
        }
        if _etag_matches(request, etag):
            _log_tile_event(
                logging.INFO,
                "tile_cache_304",
                request_id=request_id,
                z=z,
                x=x,
                y=y,
                dataset_id=dataset_id,
                min_points=min_points,
                mode=road_tile_mode,
                bytes=len(cached_tile),
                source="memory_cache",
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return Response(status_code=304, headers=headers)
        _log_tile_event(
            logging.INFO,
            "tile_cache_hit",
            request_id=request_id,
            z=z,
            x=x,
            y=y,
            dataset_id=dataset_id,
            min_points=min_points,
            mode=road_tile_mode,
            bytes=len(cached_tile),
            source="memory_cache",
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return Response(
            content=cached_tile,
            media_type="application/vnd.mapbox-vector-tile",
            headers=headers,
        )

    tile = None
    source = f"sql_mode_{road_tile_mode}"
    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            def _query_legacy_mvt_function() -> Optional[bytes]:
                cur.execute(
                    "SELECT to_regprocedure('public.get_cv_roads_mvt(integer,integer,integer,integer,text)')"
                )
                has_mvt_function_with_dataset = bool((cur.fetchone() or [None])[0])
                cur.execute(
                    "SELECT to_regprocedure('public.get_cv_roads_mvt(integer,integer,integer,integer)')"
                )
                has_mvt_function = bool((cur.fetchone() or [None])[0])
                if has_mvt_function_with_dataset and dataset_id and dataset_id != "__all__":
                    cur.execute(
                        "SELECT public.get_cv_roads_mvt(%s, %s, %s, %s, %s)",
                        (z, x, y, min_points, dataset_id),
                    )
                    row = cur.fetchone()
                    return row[0] if row and row[0] is not None else b""
                if has_mvt_function and not (dataset_id and dataset_id != "__all__"):
                    cur.execute(
                        "SELECT public.get_cv_roads_mvt(%s, %s, %s, %s)",
                        (z, x, y, min_points),
                    )
                    row = cur.fetchone()
                    return row[0] if row and row[0] is not None else b""
                return None

            def _has_column(table: str, column: str) -> bool:
                # Use pg_attribute so tables and materialized views work consistently.
                cur.execute(
                    """
                    SELECT 1
                    FROM pg_attribute
                    WHERE attrelid = to_regclass(%s)
                      AND attname = %s
                      AND attnum > 0
                      AND NOT attisdropped
                    LIMIT 1
                    """,
                    (table, column),
                )
                return cur.fetchone() is not None

            candidates = _road_source_candidates(cur, dataset_id)
            table = candidates[0] if candidates else None
            if not table:
                source = "sql_no_source_table"
                _log_tile_event(
                    logging.WARNING,
                    "tile_no_source_table",
                    request_id=request_id,
                    z=z,
                    x=x,
                    y=y,
                    dataset_id=dataset_id,
                    min_points=min_points,
                    mode=road_tile_mode,
                )
                legacy_tile = _query_legacy_mvt_function()
                if legacy_tile is None:
                    tile = b""
                else:
                    source = "legacy_mvt_function"
                    tile = legacy_tile
            else:
                has_geom_3857 = _has_column(table, "geom_3857")
                has_geom = _has_column(table, "geom")
                if not has_geom_3857 and not has_geom:
                    source = "sql_invalid_geometry"
                    _log_tile_event(
                        logging.WARNING,
                        "tile_source_missing_geometry",
                        request_id=request_id,
                        z=z,
                        x=x,
                        y=y,
                        dataset_id=dataset_id,
                        min_points=min_points,
                        mode=road_tile_mode,
                        source_table=table,
                    )
                    tile = b""
                else:
                    source = f"sql_query_{road_tile_mode}"
                    source_geom_col = "geom_3857" if has_geom_3857 else "geom"
                    geom_expr = (
                        f"r.{source_geom_col}"
                        if has_geom_3857
                        else "ST_Transform(r.geom, 3857)"
                    )
                    where_parts = [f"r.{source_geom_col} IS NOT NULL", f"{geom_expr} && bounds.geom"]
                    params = {"z": z, "x": x, "y": y, "dataset_id": dataset_id}
                    if dataset_id and dataset_id != "__all__" and _has_column(table, "dataset_id"):
                        where_parts.append("dataset_id = %(dataset_id)s")
                    if _has_column(table, "point_count"):
                        where_parts.append("point_count >= %(min_points)s")
                        params["min_points"] = min_points

                    road_segment_expr = (
                        "road_segment_id::text"
                        if _has_column(table, "road_segment_id")
                        else (
                            "segment_id::text"
                            if _has_column(table, "segment_id")
                            else ("way_id::text" if _has_column(table, "way_id") else "'unknown'")
                        )
                    )
                    has_way_id = _has_column(table, "way_id")
                    has_highway = _has_column(table, "highway")
                    road_name_expr = (
                        "road_name::text"
                        if _has_column(table, "road_name")
                        else (
                            "label::text"
                            if _has_column(table, "label")
                            else (
                                "ref::text"
                                if _has_column(table, "ref")
                                else (
                                    "name::text"
                                    if _has_column(table, "name")
                                    else (
                                        "CASE "
                                        "WHEN highway IS NOT NULL THEN initcap(replace(highway, '_', ' '))"
                                        + (" || ' #' || way_id::text" if has_way_id else "")
                                        + " ELSE NULL::text END"
                                        if has_highway
                                        else "NULL::text"
                                    )
                                )
                            )
                        )
                    )
                    direction_expr = "direction::text" if _has_column(table, "direction") else "NULL::text"
                    point_count_expr = "point_count" if _has_column(table, "point_count") else "NULL::bigint"
                    base_fields = [
                        f"{road_segment_expr} AS road_segment_id",
                        f"{road_name_expr} AS road_name",
                        f"{direction_expr} AS direction",
                        f"{point_count_expr} AS point_count",
                    ]
                    # Always include all speed + vehicle fields so popups can show both.
                    avg_speed_expr = "avg_speed_mph" if _has_column(table, "avg_speed_mph") else "NULL::float8"
                    min_speed_expr = "min_speed_mph" if _has_column(table, "min_speed_mph") else "NULL::float8"
                    max_speed_expr = "max_speed_mph" if _has_column(table, "max_speed_mph") else "NULL::float8"
                    p50_speed_expr = "p50_speed_mph" if _has_column(table, "p50_speed_mph") else "NULL::float8"
                    p90_speed_expr = "p90_speed_mph" if _has_column(table, "p90_speed_mph") else "NULL::float8"
                    speed_limit_expr = (
                        "avg_speed_limit_mph"
                        if _has_column(table, "avg_speed_limit_mph")
                        else ("speed_limit_mph" if _has_column(table, "speed_limit_mph") else "NULL::float8")
                    )
                    unique_vehicles_expr = (
                        "unique_vehicles_total"
                        if _has_column(table, "unique_vehicles_total")
                        else "NULL::bigint"
                    )
                    has_hourly_unique_json = _has_column(table, "hourly_unique_vehicles_json")
                    avg_veh_per_hour_expr = (
                        "("
                        "SELECT SUM("
                        "CASE "
                        "WHEN h.key ~ '^(?:[01]?\\d|2[0-3])$' "
                        "THEN GREATEST("
                        "CASE WHEN trim(h.value) ~ '^[-+]?\\d+(?:\\.\\d+)?$' THEN h.value::float8 ELSE 0::float8 END,"
                        "0::float8"
                        ") "
                        "ELSE 0::float8 "
                        "END"
                        ") / 24.0 "
                        "FROM jsonb_each_text(COALESCE(hourly_unique_vehicles_json, '{}'::jsonb)) AS h(key, value)"
                        ")"
                        if has_hourly_unique_json
                        else (
                            "avg_unique_vehicles_per_hour"
                            if _has_column(table, "avg_unique_vehicles_per_hour")
                            else "NULL::float8"
                        )
                    )
                    hourly_json_expr = (
                        "hourly_unique_vehicles_json::text"
                        if has_hourly_unique_json
                        else "NULL::text"
                    )
                    metric_fields = [
                        f"{avg_speed_expr} AS avg_speed_mph",
                        f"{min_speed_expr} AS min_speed_mph",
                        f"{max_speed_expr} AS max_speed_mph",
                        f"{p50_speed_expr} AS p50_speed_mph",
                        f"{p90_speed_expr} AS p90_speed_mph",
                        f"{speed_limit_expr} AS speed_limit_mph",
                        f"{unique_vehicles_expr} AS unique_vehicles_total",
                        f"{avg_veh_per_hour_expr} AS avg_unique_vehicles_per_hour",
                        f"{hourly_json_expr} AS hourly_unique_vehicles_json",
                    ]
                    select_fields_sql = ",\n                            ".join(base_fields + metric_fields)

                    query = f"""
                    WITH bounds AS (
                      SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS geom
                    ),
                    mvtgeom AS (
                      SELECT
                        {select_fields_sql},
                        ST_AsMVTGeom(
                          CASE
                            WHEN %(z)s <= 7 THEN ST_Simplify({geom_expr}, 200)
                            WHEN %(z)s <= 9 THEN ST_Simplify({geom_expr}, 80)
                            WHEN %(z)s <= 11 THEN ST_Simplify({geom_expr}, 30)
                            ELSE {geom_expr}
                          END,
                          bounds.geom,
                          4096,
                          64,
                          true
                        ) AS geom
                      FROM {table} r, bounds
                      WHERE {" AND ".join(where_parts)}
                    )
                    SELECT ST_AsMVT(mvtgeom, 'roads', 4096, 'geom') AS tile
                    FROM mvtgeom;
                    """

                    cur.execute(query, params)
                    row = cur.fetchone()
                    tile = row[0] if row else None
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        _log_tile_event(
            logging.ERROR,
            "tile_backend_error",
            request_id=request_id,
            z=z,
            x=x,
            y=y,
            dataset_id=dataset_id,
            min_points=min_points,
            mode=road_tile_mode,
            source=source,
            error_code=_tile_error_code(e),
            error=f"{type(e).__name__}: {e}",
            elapsed_ms=elapsed_ms,
        )
        return Response(
            content=b"",
            media_type="application/vnd.mapbox-vector-tile",
            status_code=503,
            headers={
                "Cache-Control": _FAILURE_TILE_CACHE_CONTROL,
                "X-Request-ID": request_id,
                "X-Road-Mode": road_tile_mode,
            },
        )

    tile_bytes = tile or b""
    _road_tile_cache_put(dataset_id, road_tile_mode, z, x, y, tile_bytes, min_points)
    etag = _weak_etag(tile_bytes)
    cache_control = _TILE_CACHE_CONTROL if tile_bytes else _EMPTY_TILE_CACHE_CONTROL
    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "X-Request-ID": request_id,
        "X-Road-Mode": road_tile_mode,
    }
    if _etag_matches(request, etag):
        _log_tile_event(
            logging.INFO,
            "tile_not_modified",
            request_id=request_id,
            z=z,
            x=x,
            y=y,
            dataset_id=dataset_id,
            min_points=min_points,
            mode=road_tile_mode,
            bytes=len(tile_bytes),
            source=source,
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return Response(status_code=304, headers=headers)

    _log_tile_event(
        logging.INFO,
        "tile_success",
        request_id=request_id,
        z=z,
        x=x,
        y=y,
        dataset_id=dataset_id,
        min_points=min_points,
        mode=road_tile_mode,
        bytes=len(tile_bytes),
        source=source,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
    )

    return Response(
        content=tile_bytes,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )

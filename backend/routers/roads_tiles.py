from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from dynamic_analyst import postgis_store
from dynamic_analyst.services.cv import latest_cv_dataset_id as _latest_cv_dataset_id

router = APIRouter()
logger = logging.getLogger("adk_server")

_ROAD_TILE_CACHE: dict[str, tuple[float, bytes]] = {}
_ROAD_TILE_CACHE_TTL_S = 180.0
_ROAD_TILE_CACHE_MAX_ENTRIES = 24
_ROAD_TILE_CACHE_MAX_TILE_BYTES = 12_000_000
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def clear_road_tiles_cache() -> None:
    _ROAD_TILE_CACHE.clear()


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
        return _safe_schema(row[0] if row else None)
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


def _road_tile_cache_key(dataset_id: str, z: int, x: int, y: int, min_points: int = 20) -> str:
    return f"{dataset_id}:{min_points}:{z}:{x}:{y}"


def _road_tile_cache_get(dataset_id: str, z: int, x: int, y: int, min_points: int = 20) -> Optional[bytes]:
    key = _road_tile_cache_key(dataset_id, z, x, y, min_points)
    item = _ROAD_TILE_CACHE.get(key)
    if not item:
        return None
    ts, tile = item
    now = time.perf_counter()
    if now - ts > _ROAD_TILE_CACHE_TTL_S:
        _ROAD_TILE_CACHE.pop(key, None)
        return None

    # Move to end as a simple LRU behavior.
    _ROAD_TILE_CACHE.pop(key, None)
    _ROAD_TILE_CACHE[key] = (now, tile)
    return tile


def _road_tile_cache_put(dataset_id: str, z: int, x: int, y: int, tile: bytes, min_points: int = 20) -> None:
    if not tile or len(tile) > _ROAD_TILE_CACHE_MAX_TILE_BYTES:
        return

    key = _road_tile_cache_key(dataset_id, z, x, y, min_points)
    now = time.perf_counter()
    _ROAD_TILE_CACHE[key] = (now, tile)

    expired = [k for k, (ts, _) in _ROAD_TILE_CACHE.items() if now - ts > _ROAD_TILE_CACHE_TTL_S]
    for cache_key in expired:
        _ROAD_TILE_CACHE.pop(cache_key, None)

    while len(_ROAD_TILE_CACHE) > _ROAD_TILE_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(_ROAD_TILE_CACHE))
        _ROAD_TILE_CACHE.pop(oldest_key, None)


@router.get("/tiles/roads/{z}/{x}/{y}.mvt")
def road_segment_tiles(
    z: int,
    x: int,
    y: int,
    dataset_id: Optional[str] = None,
    min_points: int = 20,
):
    dataset_id = dataset_id or os.environ.get("DEFAULT_CV_DATASET") or _latest_cv_dataset_id()
    min_points = max(0, int(min_points))
    cache_dataset_id = dataset_id or "__all__"

    cached_tile = _road_tile_cache_get(cache_dataset_id, z, x, y, min_points)
    if cached_tile is not None:
        logger.info(
            "road_segment_tiles(cache): z=%s x=%s y=%s dataset_id=%s min_points=%s bytes=%s",
            z,
            x,
            y,
            cache_dataset_id,
            min_points,
            len(cached_tile),
        )
        return Response(
            content=cached_tile,
            media_type="application/vnd.mapbox-vector-tile",
            headers={"Cache-Control": "public, max-age=300"},
        )

    t0 = time.perf_counter()
    tile = None
    with postgis_store._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regprocedure('public.get_cv_roads_mvt(integer,integer,integer,integer,text)')"
            )
            has_mvt_function_with_dataset = bool((cur.fetchone() or [None])[0])
            cur.execute("SELECT to_regprocedure('public.get_cv_roads_mvt(integer,integer,integer,integer)')")
            has_mvt_function = bool((cur.fetchone() or [None])[0])
            if has_mvt_function_with_dataset and dataset_id and dataset_id != "__all__":
                cur.execute("SELECT public.get_cv_roads_mvt(%s, %s, %s, %s, %s)", (z, x, y, min_points, dataset_id))
                row = cur.fetchone()
                tile = row[0] if row and row[0] is not None else b""
            elif has_mvt_function and not (dataset_id and dataset_id != "__all__"):
                cur.execute("SELECT public.get_cv_roads_mvt(%s, %s, %s, %s)", (z, x, y, min_points))
                row = cur.fetchone()
                tile = row[0] if row and row[0] is not None else b""
            else:
                if not dataset_id:
                    raise HTTPException(status_code=400, detail="Missing dataset_id")
                candidates = _road_source_candidates(cur, dataset_id)
                table = candidates[0] if candidates else None
                if not table:
                    raise HTTPException(status_code=404, detail=f"No road source table found for dataset '{dataset_id}'")

                def _has_column(table_name: str, column: str) -> bool:
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
                        (table_name, column),
                    )
                    return cur.fetchone() is not None

                has_geom_3857 = _has_column(table, "geom_3857")
                has_geom = _has_column(table, "geom")
                if not has_geom_3857 and not has_geom:
                    raise HTTPException(status_code=500, detail=f"Road source table '{table}' has no geom/geom_3857 column")

                source_geom_col = "geom_3857" if has_geom_3857 else "geom"
                geom_expr = (
                    f"r.{source_geom_col}"
                    if has_geom_3857
                    else "ST_Transform(r.geom, 3857)"
                )
                where_parts = [f"r.{source_geom_col} IS NOT NULL", f"{geom_expr} && bounds.geom"]
                params = {"z": z, "x": x, "y": y, "dataset_id": dataset_id, "min_points": min_points}
                if dataset_id and dataset_id != "__all__" and _has_column(table, "dataset_id"):
                    where_parts.append("dataset_id = %(dataset_id)s")
                if _has_column(table, "point_count"):
                    where_parts.append("point_count >= %(min_points)s")

                road_segment_expr = (
                    "road_segment_id::text"
                    if _has_column(table, "road_segment_id")
                    else ("segment_id::text" if _has_column(table, "segment_id") else "'unknown'")
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
                start_ts_expr = "start_ts::text" if _has_column(table, "start_ts") else "NULL::text"
                end_ts_expr = "end_ts::text" if _has_column(table, "end_ts") else "NULL::text"
                unique_vehicles_expr = "unique_vehicles_total" if _has_column(table, "unique_vehicles_total") else "NULL::bigint"
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

                query = f"""
                WITH bounds AS (
                  SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS geom
                ),
                mvtgeom AS (
                  SELECT
                    {road_segment_expr} AS road_segment_id,
                    {road_name_expr} AS road_name,
                    {direction_expr} AS direction,
                    {point_count_expr} AS point_count,
                    {avg_speed_expr} AS avg_speed_mph,
                    {min_speed_expr} AS min_speed_mph,
                    {max_speed_expr} AS max_speed_mph,
                    {p50_speed_expr} AS p50_speed_mph,
                    {p90_speed_expr} AS p90_speed_mph,
                    {speed_limit_expr} AS speed_limit_mph,
                    {start_ts_expr} AS start_ts,
                    {end_ts_expr} AS end_ts,
                    {unique_vehicles_expr} AS unique_vehicles_total,
                    {avg_veh_per_hour_expr} AS avg_unique_vehicles_per_hour,
                    {hourly_json_expr} AS hourly_unique_vehicles_json,
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

    _road_tile_cache_put(cache_dataset_id, z, x, y, tile or b"", min_points)

    logger.info(
        "road_segment_tiles: z=%s x=%s y=%s dataset_id=%s min_points=%s bytes=%s elapsed_ms=%.1f",
        z,
        x,
        y,
        cache_dataset_id,
        min_points,
        len(tile) if tile else 0,
        (time.perf_counter() - t0) * 1000,
    )

    return Response(
        content=tile or b"",
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=300"},
    )

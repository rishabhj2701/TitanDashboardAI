from __future__ import annotations

import logging
import re
import time
from typing import Optional

from fastapi import APIRouter

from dynamic_analyst import postgis_store

router = APIRouter()
logger = logging.getLogger("adk_server")

_ROADS_BBOX_CACHE: dict[str, tuple[float, dict]] = {}
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def clear_roads_bbox_cache() -> None:
    _ROADS_BBOX_CACHE.clear()


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


@router.get("/api/roads/bbox")
def roads_bbox(dataset_id: Optional[str] = None):
    t0 = time.perf_counter()
    cache_key = dataset_id or "__default__"
    cached = _ROADS_BBOX_CACHE.get(cache_key)
    if cached and (time.perf_counter() - cached[0]) < 300:
        return cached[1]
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
                return {"bbox": None}

            has_geom_3857 = _has_column(table, "geom_3857")
            geom_col = "geom_3857" if has_geom_3857 else "geom"
            geom_expr = f"ST_Transform({geom_col}, 4326)"

            where_parts = [f"{geom_col} IS NOT NULL"]
            params: list = []
            if dataset_id and dataset_id != "__all__" and _has_column(table, "dataset_id"):
                where_parts.append("dataset_id = %s")
                params.append(dataset_id)

            bbox_sql = f"""
                SELECT ST_XMin(ext), ST_YMin(ext), ST_XMax(ext), ST_YMax(ext)
                FROM (
                  SELECT ST_Extent({geom_expr}) AS ext
                  FROM {table}
                  WHERE {" AND ".join(where_parts)}
                ) s
            """
            cur.execute(bbox_sql, params)
            row = cur.fetchone()
            if not row or any(v is None for v in row):
                logger.info(
                    "roads_bbox: empty bbox table=%s dataset_id=%s (%.1f ms)",
                    table,
                    dataset_id,
                    (time.perf_counter() - t0) * 1000,
                )
                return {"bbox": None}
            minx, miny, maxx, maxy = row
            payload = {"bbox": {"minLon": minx, "minLat": miny, "maxLon": maxx, "maxLat": maxy}}
            _ROADS_BBOX_CACHE[cache_key] = (time.perf_counter(), payload)
            logger.info(
                "roads_bbox: table=%s dataset_id=%s (%.1f ms)",
                table,
                dataset_id,
                (time.perf_counter() - t0) * 1000,
            )
            return payload
    except Exception as exc:
        logger.warning("roads_bbox failed: %s", exc)
        return {"bbox": None}

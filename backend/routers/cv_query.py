from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from dynamic_analyst import postgis_store
from dynamic_analyst.services.cv import _cv_relation_candidates, latest_cv_dataset_id as _latest_cv_dataset_id
from dynamic_analyst.session_state import set_active_session

router = APIRouter()
logger = logging.getLogger("adk_server")

_ROAD_ATTR_KEYS = (
    "road",
    "RoadName",
    "roadName",
    "road_name",
    "ref",
    "Ref",
    "route",
    "Route",
    "highway_ref",
    "highwayRef",
    "original_road",
)


class CVQueryRequest(BaseModel):
    dataset_id: Optional[str] = None
    road_name: Optional[str] = None
    limit: Optional[int] = 25000
    min_speed: Optional[float] = None
    max_speed: Optional[float] = None
    source_table: Optional[str] = None


class CVAggregateRoadsRequest(BaseModel):
    dataset_id: Optional[str] = None
    source_table: Optional[str] = None
    min_points: Optional[int] = None
    road_name: Optional[str] = None
    road_names: Optional[list[str]] = None
    road_segment_id: Optional[str] = None
    limit: Optional[int] = None


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id header")
    set_active_session(x_session_id)
    return x_session_id


def _attrs_road_name_expr(attrs_expr: str = "attrs") -> str:
    parts = [f"NULLIF({attrs_expr}->>'{key}','')" for key in _ROAD_ATTR_KEYS]
    return "COALESCE(" + ", ".join(parts) + ")"


def _road_search_patterns(raw_value: str) -> list[str]:
    raw = (raw_value or "").strip().upper()
    raw = " ".join(raw.split())
    if not raw:
        return []

    patterns: set[str] = {raw, raw.replace("-", " ")}

    def _add_route_patterns(prefixes: list[str], number: str) -> None:
        for p in prefixes:
            patterns.add(f"{p} {number}")
            patterns.add(f"{p}-{number}")
            patterns.add(f"{p}{number}")

    match_i = re.search(r"(?:^|\b)(?:I|IS|INTERSTATE)\s*-?\s*([0-9A-Z]+)(?:\b|$)", raw)
    if match_i:
        _add_route_patterns(["IS", "I", "INTERSTATE"], match_i.group(1))

    match_us = re.search(r"(?:^|\b)(?:US|U\.S\.|U S)\s*-?\s*([0-9A-Z]+)(?:\b|$)", raw)
    if match_us:
        _add_route_patterns(["US", "U.S."], match_us.group(1))

    match_mo = re.search(r"(?:^|\b)(?:MO|STATE|SR|HIGHWAY|HWY)\s*-?\s*([0-9A-Z]+)(?:\b|$)", raw)
    if match_mo:
        _add_route_patterns(["MO", "STATE", "SR", "HIGHWAY", "HWY"], match_mo.group(1))

    ordered = sorted(patterns, key=len, reverse=True)
    deduped: list[str] = []
    seen: set[str] = set()
    for pattern in ordered:
        key = pattern.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(pattern)
    return deduped


@router.post("/api/cv/query")
def query_cv_data(
    payload: CVQueryRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)

    try:
        allowed_tables = {
            "cv_points": "cv_points",
            "cv_points_sample_medium": "cv_points_sample_medium",
        }
        source_table = allowed_tables.get(payload.source_table or "", "cv_points")
        road_expr = _attrs_road_name_expr("attrs")
        speed_expr = (
            "COALESCE("
            "NULLIF(attrs->>'speed','')::numeric, "
            "NULLIF(attrs->>'SpeedMPH','')::numeric, "
            "NULLIF(attrs->>'speed_mph','')::numeric, "
            "NULLIF(attrs->>'speedMPH','')::numeric"
            ")"
        )
        limit_expr = (
            "COALESCE("
            "NULLIF(attrs->>'SpeedLimitMPH','')::numeric, "
            "NULLIF(attrs->>'speedLimit','')::numeric, "
            "NULLIF(attrs->>'speed_limit_mph','')::numeric, "
            "NULLIF(attrs->>'speedlimit_mph','')::numeric, "
            "NULLIF(attrs->>'speed_limit','')::numeric, "
            "1"
            ")"
        )

        filters: list[str] = []
        params: dict[str, Any] = {}

        if payload.road_name:
            patterns = _road_search_patterns(payload.road_name)
            if patterns:
                if len(patterns) == 1:
                    filters.append(f"({road_expr}) ILIKE %(road_name_0)s")
                    params["road_name_0"] = f"%{patterns[0]}%"
                else:
                    road_or = []
                    for idx, pattern in enumerate(patterns):
                        key = f"road_name_{idx}"
                        road_or.append(f"({road_expr}) ILIKE %({key})s")
                        params[key] = f"%{pattern}%"
                    filters.append("(" + " OR ".join(road_or) + ")")

        if payload.min_speed is not None:
            filters.append(f"{speed_expr} >= %(min_speed)s")
            params["min_speed"] = payload.min_speed

        if payload.max_speed is not None:
            filters.append(f"{speed_expr} <= %(max_speed)s")
            params["max_speed"] = payload.max_speed

        if payload.dataset_id:
            filters.append("dataset_id = %(dataset_id)s")
            params["dataset_id"] = payload.dataset_id

        where_clause = " AND ".join(filters) if filters else "TRUE"

        grid_deg = 0.0015
        per_cell = 5

        accx_expr = "CASE WHEN NULLIF(attrs->>'AccX','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (attrs->>'AccX')::float8 END"
        accy_expr = "CASE WHEN NULLIF(attrs->>'AccY','') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (attrs->>'AccY')::float8 END"

        query = f"""
        WITH filtered AS (
            SELECT
                id::text as id,
                'Vehicle' as type,
                lon as longitude,
                lat as latitude,
                ts as timestamp,
                {speed_expr} as speed,
                0 as bearing,
                {accx_expr} as acc_x,
                {accy_expr} as acc_y,
                {limit_expr} as "speedLimit",
                {road_expr} as "roadName",
                attrs->>'VehicleID' as vehicle_id,
                attrs->>'county' as county,
                attrs->>'func_class' as func_class,
                attrs->>'original_road' as original_road,
                road_segment_id,
                road_dist_m,
                road_conf,
                FLOOR(lat / %(grid_deg)s) AS lat_bin,
                FLOOR(lon / %(grid_deg)s) AS lon_bin,
                md5(
                    COALESCE(id::text,'') ||
                    COALESCE(attrs->>'VehicleID','') ||
                    COALESCE(ts::text,'') ||
                    COALESCE({road_expr}, '')
                ) AS stable_key
            FROM {source_table}
            WHERE {where_clause}
            AND lat IS NOT NULL
            AND lon IS NOT NULL
        ),
        ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY lat_bin, lon_bin
                    ORDER BY stable_key
                ) AS rn
            FROM filtered
        )
        SELECT
            id, type, longitude, latitude, timestamp, speed, bearing, acc_x, acc_y,
            "speedLimit", "roadName", vehicle_id, county, func_class, original_road,
            road_segment_id, road_dist_m, road_conf
        FROM ranked
        WHERE rn <= %(per_cell)s
        ORDER BY stable_key
        LIMIT %(limit)s
        """
        params["limit"] = payload.limit or 25000
        params["grid_deg"] = grid_deg
        params["per_cell"] = per_cell

        with postgis_store._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        results = []
        for row in rows:
            item = dict(row)
            item["acceleration"] = {
                "x": float(item.pop("acc_x", 0) or 0),
                "y": float(item.pop("acc_y", 0) or 0),
            }
            for field in ["speed", "speedLimit", "bearing", "longitude", "latitude"]:
                if item.get(field) is not None:
                    item[field] = float(item[field])
            item = {k: v for k, v in item.items() if v is not None}
            results.append(item)

        return {
            "status": "success",
            "count": len(results),
            "data": results,
            "filters_applied": {
                "road_name": payload.road_name,
                "min_speed": payload.min_speed,
                "max_speed": payload.max_speed,
                "limit": payload.limit,
            },
        }
    except Exception as exc:
        logger.error("CV data query failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/cv/aggregate-roads")
def query_cv_road_aggregates(
    payload: CVAggregateRoadsRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)

    try:
        dataset_id = payload.dataset_id or _latest_cv_dataset_id()
        min_points = payload.min_points if payload.min_points is not None else 140
        filters: list[str] = []
        params: dict[str, Any] = {}
        features: list[dict] = []

        with postgis_store._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                def _regclass_name(name: Optional[str]) -> Optional[str]:
                    if not name:
                        return None
                    cur.execute("SELECT to_regclass(%s)::text AS relname", (name,))
                    row = cur.fetchone() or {}
                    return row.get("relname")

                source_candidates: list[str] = []
                if payload.source_table:
                    source_candidates.append(payload.source_table)
                for relation_name in ("cv_road_stats_mv", "cv_road_agg"):
                    try:
                        source_candidates.extend(_cv_relation_candidates(conn, relation_name))
                    except Exception:
                        continue
                source_candidates.extend(
                    [
                        "cv_road_stats_mv",
                        "public.cv_road_stats_mv",
                        "cv_road_agg",
                        "public.cv_road_agg",
                    ]
                )
                deduped_candidates: list[str] = []
                seen_candidates: set[str] = set()
                for candidate in source_candidates:
                    item = str(candidate or "").strip()
                    if not item or item in seen_candidates:
                        continue
                    seen_candidates.add(item)
                    deduped_candidates.append(item)

                source_table: Optional[str] = None
                for candidate in deduped_candidates:
                    rel = _regclass_name(candidate)
                    if rel:
                        source_table = rel
                        break

                if not source_table:
                    return {
                        "status": "success",
                        "dataset_id": dataset_id,
                        "count": 0,
                        "geojson": {"type": "FeatureCollection", "features": []},
                    }

                def _has_column(table: str, column: str) -> bool:
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

                def _pick_column(table: str, options: list[str]) -> Optional[str]:
                    for column in options:
                        if _has_column(table, column):
                            return column
                    return None

                geom_col = _pick_column(source_table, ["geom", "geom_4326", "geom_3857"])
                if not geom_col:
                    raise ValueError(f"Road aggregate source '{source_table}' has no geometry column.")

                geom_expr = "ST_Transform(geom_3857, 4326)" if geom_col == "geom_3857" else geom_col

                road_id_col = _pick_column(source_table, ["road_segment_id", "way_id", "segment_id", "road_id"])
                point_count_col = _pick_column(source_table, ["point_count", "points", "count"])
                avg_speed_col = _pick_column(source_table, ["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
                min_speed_col = _pick_column(source_table, ["min_speed_mph", "min_speed"])
                max_speed_col = _pick_column(source_table, ["max_speed_mph", "max_speed"])
                speed_limit_mode_col = _pick_column(
                    source_table,
                    ["speed_limit_mode", "speed_limit_mph", "avg_speed_limit_mph"],
                )
                speed_limit_p50_col = _pick_column(source_table, ["speed_limit_p50_mph", "speed_limit_p50"])
                speed_limit_p90_col = _pick_column(source_table, ["speed_limit_p90_mph", "speed_limit_p90"])
                start_ts_col = _pick_column(source_table, ["start_ts"])
                end_ts_col = _pick_column(source_table, ["end_ts"])

                def _nullif_text(column: str) -> str:
                    return f"NULLIF({column}::text, '')"

                label_parts: list[str] = []
                for col in ("road_name", "label", "ref", "name"):
                    if _has_column(source_table, col):
                        label_parts.append(_nullif_text(col))
                if _has_column(source_table, "highway"):
                    highway_suffix = " || ' #' || way_id::text" if _has_column(source_table, "way_id") else ""
                    label_parts.append(
                        "CASE "
                        "WHEN NULLIF(highway::text, '') IS NOT NULL "
                        f"THEN initcap(replace(highway::text, '_', ' ')){highway_suffix} "
                        "ELSE NULL END"
                    )
                if road_id_col:
                    label_parts.append(f"'Way ' || {road_id_col}::text")
                else:
                    label_parts.append("'[unknown]'")
                road_name_expr = f"COALESCE({', '.join(label_parts)})"

                filters.append(f"{geom_col} IS NOT NULL")
                if point_count_col:
                    filters.append(f"{point_count_col} >= %(min_points)s")
                    params["min_points"] = min_points

                if dataset_id and _has_column(source_table, "dataset_id"):
                    filters.append("dataset_id = %(dataset_id)s")
                    params["dataset_id"] = dataset_id

                if payload.road_segment_id:
                    if road_id_col:
                        filters.append(f"{road_id_col}::text = %(road_segment_id)s")
                        params["road_segment_id"] = payload.road_segment_id
                    else:
                        filters.append("FALSE")

                road_or_clauses: list[str] = []
                if payload.road_name:
                    raw = str(payload.road_name).strip().upper()
                    raw = " ".join(raw.split())

                    patterns: set[str] = set()
                    if raw:
                        patterns.add(f"%{raw}%")
                        patterns.add(f"%{raw.replace('-', ' ')}%")

                        def _add_route_patterns(prefixes: list[str], num: str) -> None:
                            for p in prefixes:
                                patterns.add(f"%{p} {num}%")
                                patterns.add(f"%{p}-{num}%")

                        match_i = re.search(r"(?:I|IS|INTERSTATE)\\s*-?\\s*([0-9A-Z]+)", raw)
                        if match_i:
                            _add_route_patterns(["IS", "I", "INTERSTATE"], match_i.group(1))

                        match_us = re.search(r"(?:US|U\\.S\\.|U S)\\s*-?\\s*([0-9A-Z]+)", raw)
                        if match_us:
                            _add_route_patterns(["US", "U.S."], match_us.group(1))

                        match_mo = re.search(r"(?:MO|STATE|SR|HIGHWAY|HWY)\\s*-?\\s*([0-9A-Z]+)", raw)
                        if match_mo:
                            _add_route_patterns(["MO", "STATE", "SR", "HIGHWAY", "HWY"], match_mo.group(1))

                    for idx, pat in enumerate(sorted(patterns)):
                        key = f"road_name_pattern_{idx}"
                        road_or_clauses.append(f"{road_name_expr} ILIKE %({key})s")
                        params[key] = pat

                if payload.road_names:
                    normalized_names = [
                        str(name).strip()
                        for name in payload.road_names
                        if isinstance(name, str) and str(name).strip()
                    ]
                    deduped_names: list[str] = []
                    seen_names: set[str] = set()
                    for name in normalized_names:
                        key = name.lower()
                        if key in seen_names:
                            continue
                        seen_names.add(key)
                        deduped_names.append(name)
                        if len(deduped_names) >= 200:
                            break
                    for idx, name in enumerate(deduped_names):
                        key = f"road_name_exact_{idx}"
                        road_or_clauses.append(f"LOWER({road_name_expr}) = LOWER(%({key})s)")
                        params[key] = name

                if road_or_clauses:
                    filters.append("(" + " OR ".join(road_or_clauses) + ")")

                where_clause = " AND ".join(filters)
                limit_clause = "LIMIT %(limit)s" if payload.limit else ""
                if payload.limit:
                    params["limit"] = payload.limit

                road_id_expr = f"{road_id_col}::text" if road_id_col else "NULL::text"
                point_count_expr = point_count_col if point_count_col else "NULL::bigint"
                avg_speed_expr = avg_speed_col if avg_speed_col else "NULL::float8"
                min_speed_expr = min_speed_col if min_speed_col else "NULL::float8"
                max_speed_expr = max_speed_col if max_speed_col else "NULL::float8"
                speed_limit_mode_expr = speed_limit_mode_col if speed_limit_mode_col else "NULL::float8"
                speed_limit_p50_expr = speed_limit_p50_col if speed_limit_p50_col else "NULL::float8"
                speed_limit_p90_expr = speed_limit_p90_col if speed_limit_p90_col else "NULL::float8"
                start_ts_expr = start_ts_col if start_ts_col else "NULL::timestamptz"
                end_ts_expr = end_ts_col if end_ts_col else "NULL::timestamptz"
                order_expr = point_count_col if point_count_col else "1"

                query = f"""
                SELECT
                    {road_id_expr} AS road_segment_id,
                    {road_name_expr} AS road_name,
                    ST_AsGeoJSON({geom_expr}) AS geom,
                    {avg_speed_expr} AS avg_speed,
                    {speed_limit_p50_expr} AS speed_limit_p50,
                    {speed_limit_p90_expr} AS speed_limit_p90,
                    {speed_limit_mode_expr} AS speed_limit_mode,
                    {min_speed_expr} AS min_speed,
                    {max_speed_expr} AS max_speed,
                    {start_ts_expr} AS start_ts,
                    {end_ts_expr} AS end_ts,
                    {point_count_expr} AS point_count
                FROM {source_table}
                WHERE {where_clause}
                ORDER BY {order_expr} DESC
                {limit_clause}
                """
                cur.execute(query, params)
                rows = cur.fetchall()

        for row in rows:
            geom_raw = row.get("geom")
            if not geom_raw:
                continue
            try:
                geom = json.loads(geom_raw)
            except Exception:
                continue

            features.append(
                {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "road_segment_id": row.get("road_segment_id"),
                        "road_name": row.get("road_name"),
                        "avg_speed_mph": float(row["avg_speed"]) if row.get("avg_speed") is not None else None,
                        "speed_limit_p50_mph": float(row["speed_limit_p50"]) if row.get("speed_limit_p50") is not None else None,
                        "speed_limit_p90_mph": float(row["speed_limit_p90"]) if row.get("speed_limit_p90") is not None else None,
                        "speed_limit_mph": float(row["speed_limit_mode"]) if row.get("speed_limit_mode") is not None else None,
                        "min_speed_mph": float(row["min_speed"]) if row.get("min_speed") is not None else None,
                        "max_speed_mph": float(row["max_speed"]) if row.get("max_speed") is not None else None,
                        "start_ts": row.get("start_ts").isoformat() if row.get("start_ts") else None,
                        "end_ts": row.get("end_ts").isoformat() if row.get("end_ts") else None,
                        "point_count": int(row["point_count"]) if row.get("point_count") is not None else 0,
                    },
                }
            )

        return {
            "status": "success",
            "dataset_id": dataset_id,
            "count": len(features),
            "geojson": {"type": "FeatureCollection", "features": features},
        }
    except Exception as exc:
        logger.error("CV road aggregate query failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

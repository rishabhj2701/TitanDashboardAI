from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ...data_registry import _safe_value
from .table_names import APP_EVENTS


def get_events_bbox(
    buffer_degrees: float,
    *,
    conn_factory,
    sid_fn,
    uid_fn,
) -> Optional[Dict[str, float]]:
    sid = sid_fn()
    uid = uid_fn()
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                MIN(lon) as min_lon, MAX(lon) as max_lon,
                MIN(lat) as min_lat, MAX(lat) as max_lat,
                COUNT(*) as count
            FROM """ + APP_EVENTS + """
            WHERE session_id = %s AND owner_user_id = %s AND lat IS NOT NULL AND lon IS NOT NULL
            """,
            (sid, uid),
        )
        row = cur.fetchone()
        if not row or row[4] == 0:
            return None

        min_lon, max_lon, min_lat, max_lat, count = row
        return {
            "min_lon": float(min_lon) - buffer_degrees,
            "max_lon": float(max_lon) + buffer_degrees,
            "min_lat": float(min_lat) - buffer_degrees,
            "max_lat": float(max_lat) + buffer_degrees,
            "event_count": int(count),
        }


def clear_roads(session_only: bool = False, *, conn_factory) -> Dict[str, int]:
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM roads")
        count_before = cur.fetchone()[0]
        cur.execute("TRUNCATE TABLE roads")
        conn.commit()
    return {"cleared": int(count_before)}


def get_roads_count(*, conn_factory) -> int:
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM roads")
        return cur.fetchone()[0]


def ingest_roads(
    features: List[Dict],
    id_field: Optional[str],
    name_field: Optional[str],
    bbox_filter: Optional[Dict[str, float]],
    clear_existing: bool,
    batch_size: int,
    *,
    conn_factory,
    detect_road_fields_fn,
) -> Dict[str, Any]:
    if not features:
        return {"error": "No features provided", "inserted": 0, "skipped": 0}

    sample_props = features[0].get("properties", {}) or {}
    detected = detect_road_fields_fn(list(sample_props.keys()))

    id_col = id_field or detected["id_field"]
    name_col = name_field or detected["name_field"]

    insert_sql = """
    INSERT INTO roads (road_segment_id, name, geom, attrs, geom_m)
    VALUES (
        %s,
        %s,
        ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
        %s::jsonb,
        ST_Transform(ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 26915)
    )
    ON CONFLICT (road_segment_id) DO UPDATE
    SET name = EXCLUDED.name,
        geom = EXCLUDED.geom,
        attrs = EXCLUDED.attrs,
        geom_m = EXCLUDED.geom_m
    """

    inserted = 0
    skipped = 0
    bbox_filtered = 0
    batch = []

    with conn_factory() as conn, conn.cursor() as cur:
        if clear_existing:
            cur.execute("TRUNCATE TABLE roads")

        for i, feat in enumerate(features):
            try:
                geom = feat.get("geometry")
                if not geom:
                    skipped += 1
                    continue

                gtype = geom.get("type")
                if gtype not in ("LineString", "MultiLineString"):
                    skipped += 1
                    continue

                if bbox_filter:
                    coords = geom.get("coordinates", [])
                    if gtype == "LineString":
                        all_coords = coords
                    else:
                        all_coords = [pt for line in coords for pt in line]

                    if all_coords:
                        lons = [c[0] for c in all_coords if len(c) >= 2]
                        lats = [c[1] for c in all_coords if len(c) >= 2]
                        if lons and lats:
                            if (
                                max(lons) < bbox_filter["min_lon"]
                                or min(lons) > bbox_filter["max_lon"]
                                or max(lats) < bbox_filter["min_lat"]
                                or min(lats) > bbox_filter["max_lat"]
                            ):
                                bbox_filtered += 1
                                continue

                props = feat.get("properties", {}) or {}

                if id_col and id_col in props:
                    road_id = str(props[id_col]).strip()
                else:
                    road_id = f"Road_{i}"

                if name_col and name_col in props:
                    road_name = str(props[name_col]).strip()
                else:
                    road_name = road_id

                attrs = {
                    k: _safe_value(v)
                    for k, v in props.items()
                    if k not in (id_col, name_col) and v is not None
                }

                geom_json = json.dumps(geom)
                batch.append((road_id, road_name, geom_json, json.dumps(attrs), geom_json))

                if len(batch) >= batch_size:
                    cur.executemany(insert_sql, batch)
                    inserted += len(batch)
                    batch.clear()
                    conn.commit()

            except Exception:
                skipped += 1
                continue

        if batch:
            cur.executemany(insert_sql, batch)
            inserted += len(batch)
            conn.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "bbox_filtered": bbox_filtered,
        "id_field_used": id_col,
        "name_field_used": name_col,
        "total_features": len(features),
    }


def remap_events_to_roads(
    max_dist_m: float,
    *,
    conn_factory,
    sid_fn,
    uid_fn,
    list_datasets_fn,
    map_events_to_roads_fn,
) -> Dict[str, Any]:
    sid = sid_fn()
    uid = uid_fn()

    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE """ + APP_EVENTS + """
            SET road_segment_id = NULL, road_dist_m = NULL, road_conf = NULL
            WHERE session_id = %s AND owner_user_id = %s
            """,
            (sid, uid),
        )
        cleared = cur.rowcount
        conn.commit()

    datasets = list_datasets_fn()

    total_results = {
        "cleared_mappings": cleared,
        "datasets_remapped": 0,
        "total_matched": 0,
        "total_events": 0,
    }

    for ds in datasets:
        if ds.get("status") == "ready":
            result = map_events_to_roads_fn(ds["dataset_id"], max_dist_m=max_dist_m)
            total_results["datasets_remapped"] += 1
            total_results["total_matched"] += result.get("matched", 0)
            total_results["total_events"] += result.get("total", 0)

    return total_results

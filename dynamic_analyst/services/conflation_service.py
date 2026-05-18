"""Conflation dataset resolution and metadata services."""

from __future__ import annotations

import re
from typing import Any

from psycopg2.extras import RealDictCursor

from ..session_state import get_active_session, get_active_user
from ..storage.postgis.table_names import APP_DATASETS, APP_EVENTS
from ..sql.catalog import (
    _cv_relation_candidates,
    _db_conn,
    _first_existing_relation,
    _latest_dataset_id_from_relation,
    _resolve_hard_brake_table,
    _table_column_names,
)
from .cv import get_active_cv_run_id


def _first_present(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def is_hard_braking_ref(text: str) -> bool:
    lowered = (text or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "", lowered)
    return (
        "hard braking" in lowered
        or "hard brake" in lowered
        or "hardbraking" in normalized
        or "hardbrake" in normalized
    )


def get_dataset_info(dataset_id: str) -> dict[str, Any]:
    """Get metadata about a dataset to determine its storage table."""
    sid = get_active_session() or "_no_session_"
    uid = (get_active_user() or "dev-user").strip() or "dev-user"

    with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if is_hard_braking_ref(dataset_id):
            hb_table = _resolve_hard_brake_table(conn)
            if hb_table:
                hb_cols = _table_column_names(conn, hb_table)
                has_dataset_filter = "dataset_id" in hb_cols
                id_col = "point_id" if "point_id" in hb_cols else ("id" if "id" in hb_cols else "point_id")
                time_col = "ts" if "ts" in hb_cols else ("timestamp" if "timestamp" in hb_cols else "ts")
                props_col = "attrs" if "attrs" in hb_cols else ("props" if "props" in hb_cols else "attrs")
                lat_col = "lat" if "lat" in hb_cols else ("latitude" if "latitude" in hb_cols else None)
                lon_col = "lon" if "lon" in hb_cols else ("longitude" if "longitude" in hb_cols else None)
                speed_col = _first_present(hb_cols, ("speed", "speed_mph", "spd_mph"))
                speed_limit_col = _first_present(hb_cols, ("speed_limit_mph", "speed_limit", "speedlimitmph", "speedlimit"))
                acc_x_col = _first_present(hb_cols, ("accx", "acc_x", "accel_x", "ax"))
                acc_y_col = _first_present(hb_cols, ("accy", "acc_y", "accel_y", "ay"))
                road_segment_col = (
                    "road_segment_id"
                    if "road_segment_id" in hb_cols
                    else ("way_id" if "way_id" in hb_cols else None)
                )
                return {
                    "dataset_id": dataset_id,
                    "dataset_filter_id": dataset_id if has_dataset_filter else None,
                    "name": "hard_braking",
                    "entity_type": "hard_braking",
                    "table": hb_table,
                    "geo_cols": {"lat": "lat", "lon": "lon", "geom_m": "geom_m"},
                    "time_col": time_col,
                    "props_col": props_col,
                    "id_col": id_col,
                    "lat_col": lat_col,
                    "lon_col": lon_col,
                    "speed_col": speed_col,
                    "speed_limit_col": speed_limit_col,
                    "acc_x_col": acc_x_col,
                    "acc_y_col": acc_y_col,
                    "road_segment_col": road_segment_col,
                    "geom_m_col": "geom_m" if "geom_m" in hb_cols else None,
                    "geom_3857_col": "geom_3857" if "geom_3857" in hb_cols else None,
                    "geom_4326_col": "geom_4326" if "geom_4326" in hb_cols else ("geom" if "geom" in hb_cols else None),
                    "has_date_range": False,
                    "has_dataset_filter": has_dataset_filter,
                }

        cur.execute(
            """
            SELECT dataset_id, name, entity_type, status, stats, mapping
            FROM """ + APP_DATASETS + """
            WHERE (owner_user_id = %s AND dataset_id = %s)
               OR (owner_user_id = %s AND lower(name) = lower(%s))
               OR (session_id = %s AND lower(name) = lower(%s))
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (uid, dataset_id, uid, dataset_id, sid, dataset_id),
        )
        row = cur.fetchone()

        if row:
            entity = str(row.get("entity_type") or "").lower()
            if entity in ("cv", "traffic", "vehicle"):
                table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points")) or "cv_points"
                props_col = "attrs"
            else:
                table = APP_EVENTS
                props_col = "props"
            table_cols = _table_column_names(conn, table)
            has_dataset_filter = "dataset_id" in table_cols
            id_col = "id" if "id" in table_cols else "id"
            time_col = "ts" if "ts" in table_cols else ("timestamp" if "timestamp" in table_cols else "ts")
            lat_col = "lat" if "lat" in table_cols else ("latitude" if "latitude" in table_cols else None)
            lon_col = "lon" if "lon" in table_cols else ("longitude" if "longitude" in table_cols else None)
            speed_col = _first_present(table_cols, ("speed", "speed_mph", "spd_mph"))
            speed_limit_col = _first_present(table_cols, ("speed_limit_mph", "speed_limit", "speedlimitmph", "speedlimit"))
            acc_x_col = _first_present(table_cols, ("accx", "acc_x", "accel_x", "ax"))
            acc_y_col = _first_present(table_cols, ("accy", "acc_y", "accel_y", "ay"))
            road_segment_col = (
                "road_segment_id"
                if "road_segment_id" in table_cols
                else ("way_id" if "way_id" in table_cols else None)
            )

            return {
                "dataset_id": row["dataset_id"],
                "dataset_filter_id": row["dataset_id"] if has_dataset_filter else None,
                "name": row["name"],
                "entity_type": entity,
                "table": table,
                "geo_cols": {"lat": "lat", "lon": "lon", "geom_m": "geom_m"},
                "time_col": time_col,
                "props_col": props_col,
                "id_col": id_col,
                "lat_col": lat_col,
                "lon_col": lon_col,
                "speed_col": speed_col,
                "speed_limit_col": speed_limit_col,
                "acc_x_col": acc_x_col,
                "acc_y_col": acc_y_col,
                "road_segment_col": road_segment_col,
                "geom_m_col": "geom_m" if "geom_m" in table_cols else None,
                "geom_3857_col": "geom_3857" if "geom_3857" in table_cols else None,
                "geom_4326_col": "geom_4326" if "geom_4326" in table_cols else ("geom" if "geom" in table_cols else None),
                "has_date_range": entity == "workzone",
                "has_dataset_filter": has_dataset_filter,
            }

        cur.execute(f"SELECT 1 FROM {APP_EVENTS} WHERE dataset_id = %s LIMIT 1", (dataset_id,))
        if cur.fetchone():
            return {
                "dataset_id": dataset_id,
                "dataset_filter_id": dataset_id,
                "table": APP_EVENTS,
                "geo_cols": {"lat": "lat", "lon": "lon", "geom_m": "geom_m"},
                "time_col": "ts",
                "props_col": "props",
                "id_col": "id",
                "lat_col": "lat",
                "lon_col": "lon",
                "speed_col": None,
                "speed_limit_col": None,
                "acc_x_col": None,
                "acc_y_col": None,
                "road_segment_col": "road_segment_id",
                "geom_m_col": "geom_m",
                "geom_3857_col": None,
                "geom_4326_col": "geom",
                "has_date_range": False,
                "has_dataset_filter": True,
            }

        cv_points_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points")) or "cv_points"
        cv_cols = _table_column_names(conn, cv_points_table)
        if "dataset_id" in cv_cols:
            cur.execute(f"SELECT 1 FROM {cv_points_table} WHERE dataset_id = %s LIMIT 1", (dataset_id,))
        else:
            cur.execute(f"SELECT 1 FROM {cv_points_table} LIMIT 1")
        if cur.fetchone():
            has_dataset_filter = "dataset_id" in cv_cols
            active_run_id = get_active_cv_run_id(uid)
            return {
                "dataset_id": dataset_id,
                "dataset_filter_id": dataset_id if has_dataset_filter else active_run_id,
                "table": cv_points_table,
                "geo_cols": {"lat": "lat", "lon": "lon", "geom_m": "geom_m"},
                "time_col": "ts" if "ts" in cv_cols else ("timestamp" if "timestamp" in cv_cols else "ts"),
                "props_col": "attrs",
                "id_col": "id" if "id" in cv_cols else "id",
                "lat_col": "lat" if "lat" in cv_cols else ("latitude" if "latitude" in cv_cols else None),
                "lon_col": "lon" if "lon" in cv_cols else ("longitude" if "longitude" in cv_cols else None),
                "speed_col": _first_present(cv_cols, ("speed", "speed_mph", "spd_mph")),
                "speed_limit_col": _first_present(cv_cols, ("speed_limit_mph", "speed_limit", "speedlimitmph", "speedlimit")),
                "acc_x_col": _first_present(cv_cols, ("accx", "acc_x", "accel_x", "ax")),
                "acc_y_col": _first_present(cv_cols, ("accy", "acc_y", "accel_y", "ay")),
                "road_segment_col": (
                    "road_segment_id"
                    if "road_segment_id" in cv_cols
                    else ("way_id" if "way_id" in cv_cols else None)
                ),
                "geom_m_col": "geom_m" if "geom_m" in cv_cols else None,
                "geom_3857_col": "geom_3857" if "geom_3857" in cv_cols else None,
                "geom_4326_col": "geom_4326" if "geom_4326" in cv_cols else ("geom" if "geom" in cv_cols else None),
                "has_date_range": False,
                "has_dataset_filter": has_dataset_filter,
            }

    raise ValueError(f"Dataset '{dataset_id}' not found in any table")


def resolve_dataset_id_generic(name: str) -> str:
    """Resolve dataset names/aliases to a concrete dataset_id."""
    sid = get_active_session() or "_no_session_"
    uid = (get_active_user() or "dev-user").strip() or "dev-user"

    if re.match(r"^[a-zA-Z]+_[0-9a-fA-F]{6,}$", name or ""):
        return name

    lowered = (name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "", lowered)
    hard_braking = is_hard_braking_ref(name)

    entity_map = {
        "crashes": "crash",
        "crash": "crash",
        "accidents": "crash",
        "accident": "crash",
        "workzones": "workzone",
        "workzone": "workzone",
        "construction": "workzone",
        "traffic": "cv",
        "cv": "cv",
        "cvdata": "cv",
        "vehicles": "cv",
    }
    entity = entity_map.get(lowered)
    if hard_braking:
        entity = "hard_braking"
    elif entity is None and "connectedvehicle" in normalized:
        entity = "cv"

    with _db_conn() as conn, conn.cursor() as cur:
        if entity and entity != "hard_braking":
            cur.execute(
                """
                SELECT dataset_id FROM """ + APP_DATASETS + """
                WHERE owner_user_id = %s AND status = 'ready' AND entity_type = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (uid, entity),
            )
        else:
            cur.execute(
                """
                SELECT dataset_id FROM """ + APP_DATASETS + """
                WHERE owner_user_id = %s AND status = 'ready' AND lower(name) = lower(%s)
                ORDER BY created_at DESC LIMIT 1
                """,
                (uid, name),
            )

        row = cur.fetchone()
        if not row and entity and entity != "hard_braking":
            cur.execute(
                """
                SELECT dataset_id FROM """ + APP_DATASETS + """
                WHERE session_id = %s AND status = 'ready' AND entity_type = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (sid, entity),
            )
            row = cur.fetchone()
        if not row and not entity:
            cur.execute(
                """
                SELECT dataset_id FROM """ + APP_DATASETS + """
                WHERE session_id = %s AND status = 'ready' AND lower(name) = lower(%s)
                ORDER BY created_at DESC LIMIT 1
                """,
                (sid, name),
            )
            row = cur.fetchone()
        if row:
            return row[0]

        if entity == "hard_braking":
            hb_table = _resolve_hard_brake_table(conn)
            if hb_table:
                hb_dataset_id = _latest_dataset_id_from_relation(conn, hb_table)
                if hb_dataset_id:
                    return hb_dataset_id
                return "hard_braking"
            cv_points_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points")) or "cv_points"
            cv_dataset_id = _latest_dataset_id_from_relation(conn, cv_points_table)
            if cv_dataset_id:
                return cv_dataset_id
            active_run_id = get_active_cv_run_id(uid)
            if active_run_id:
                return active_run_id

        if entity == "cv":
            cv_points_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points")) or "cv_points"
            cv_dataset_id = _latest_dataset_id_from_relation(conn, cv_points_table)
            if cv_dataset_id:
                return cv_dataset_id
            active_run_id = get_active_cv_run_id(uid)
            if active_run_id:
                return active_run_id

    raise ValueError(f"Dataset '{name}' not found for this user")


def build_conflation_response(
    match_df: Any,
    left_info: dict[str, Any],
    right_info: dict[str, Any],
    left_id: str,
    right_id: str,
    max_distance_meters: float,
    time_mode: str,
    time_window_minutes: int,
    *,
    generate_map: bool,
    map_point_count: int,
) -> str:
    """Build user-facing markdown response for conflation results."""
    match_count = len(match_df)
    left_unique = match_df["left_id"].nunique() if match_count > 0 else 0
    right_unique = match_df["right_id"].nunique() if match_count > 0 else 0
    left_entity = left_info.get("entity_type", "left")
    right_entity = right_info.get("entity_type", "right")

    response_parts = [
        "**Spatial-Temporal Conflation Results**",
        "",
        f"- **Left dataset**: {left_info.get('name', left_id)} ({left_info.get('entity_type', 'unknown')})",
        f"- **Right dataset**: {right_info.get('name', right_id)} ({right_info.get('entity_type', 'unknown')})",
        f"- **Max distance**: {max_distance_meters}m",
        f"- **Time mode**: {time_mode}" + (f" (±{time_window_minutes} min)" if time_mode == "window" else ""),
        "",
        "**Results:**",
        f"- Total matches: {match_count}",
        f"- Unique {left_entity} records matched: {left_unique}",
        f"- Unique {right_entity} records matched: {right_unique}",
    ]

    if match_count == 0:
        response_parts.extend(
            [
                "",
                (
                    f"No overlaps found: 0 matches, 0 {left_entity} records matched, "
                    f"and 0 {right_entity} records matched."
                ),
            ]
        )
        if time_mode == "window":
            response_parts.append(
                f"This means no records fell within {max_distance_meters}m and +/-{time_window_minutes} minutes."
            )
        else:
            response_parts.append(
                f"This means no records fell within {max_distance_meters}m under time mode '{time_mode}'."
            )
        return "\n".join(response_parts)

    if match_count > 0:
        avg_dist = match_df["distance_meters"].mean()
        response_parts.append(f"- Average distance: {avg_dist:.1f}m")

        if "time_delta_minutes" in match_df.columns and match_df["time_delta_minutes"].notna().any():
            avg_time = match_df["time_delta_minutes"].abs().mean()
            response_parts.append(f"- Average time delta: {avg_time:.1f} minutes")

        if generate_map and map_point_count > 0:
            response_parts.append("")
            response_parts.append(f"A map showing {map_point_count} matched points has been generated.")

    return "\n".join(response_parts)


__all__ = [
    "build_conflation_response",
    "get_dataset_info",
    "is_hard_braking_ref",
    "resolve_dataset_id_generic",
]

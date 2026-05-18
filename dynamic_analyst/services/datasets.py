"""Dataset discovery and map overlay services."""

from __future__ import annotations

import ast
import json
import logging
from typing import Any, Optional

from psycopg2.extras import RealDictCursor

from .. import postgis_store
from ..column_intelligence import generate_query_suggestions
from .cv import get_active_cv_run_id
from ..session_state import get_active_session, get_active_user, save_map_for_session
from ..storage.postgis.table_names import APP_DATASETS, APP_EVENTS


def _db_conn():
    return postgis_store._conn()


def _relation_exists(conn, relation_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (relation_name,))
        return cur.fetchone()[0] is not None


def _first_existing_relation(conn, candidates: list[str]) -> Optional[str]:
    for relation in candidates:
        if _relation_exists(conn, relation):
            return relation
    return None


def _table_column_names(conn, relation_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass(%s)
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (relation_name,),
        )
        return {str(row[0]).strip().lower() for row in cur.fetchall()}


def _active_cv_run_context() -> dict[str, Any]:
    try:
        uid = get_active_user() or "dev-user"
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            active_run_id = get_active_cv_run_id(uid)
            if not active_run_id:
                return {}
            cur.execute(
                """
                SELECT
                    r.run_id,
                    r.schema_name,
                    r.state_code,
                    r.point_count,
                    r.ts_start,
                    r.ts_end
                FROM public.cv_runs r
                WHERE r.run_id = %s
                LIMIT 1
                """
                ,
                (active_run_id,),
            )
            row = cur.fetchone() or {}
            run_id = row.get("run_id")
            if not run_id:
                return {}
            return {
                "run_id": run_id,
                "schema_name": row.get("schema_name"),
                "state_code": row.get("state_code"),
                "point_count": row.get("point_count"),
                "ts_start": row.get("ts_start"),
                "ts_end": row.get("ts_end"),
            }
    except Exception:
        return {}


def _active_cv_schema_name(conn) -> Optional[str]:
    try:
        with conn.cursor() as cur:
            active_run_id = get_active_cv_run_id(get_active_user())
            if not active_run_id:
                return None
            cur.execute("SELECT schema_name FROM public.cv_runs WHERE run_id=%s LIMIT 1", (active_run_id,))
            row = cur.fetchone()
            schema_name = str(row[0]).strip() if row and row[0] else ""
            return schema_name or None
    except Exception:
        return None


def _cv_relation_candidates(conn, relation_name: str) -> list[str]:
    candidates: list[str] = []
    active_schema = _active_cv_schema_name(conn)
    if active_schema:
        candidates.append(f"{active_schema}.{relation_name}")
    candidates.append(f"public.{relation_name}")
    candidates.append(relation_name)
    return list(dict.fromkeys(candidates))


def _resolve_hard_brake_table(conn) -> Optional[str]:
    candidates: list[str] = []
    candidates.extend(_cv_relation_candidates(conn, "cv_hard_brake_events_mv"))
    candidates.extend(_cv_relation_candidates(conn, "cv_hard_brake"))
    return _first_existing_relation(conn, list(dict.fromkeys(candidates)))


def _latest_dataset_id_from_relation(conn, table_name: str) -> Optional[str]:
    cols = _table_column_names(conn, table_name)
    if "dataset_id" not in cols:
        return None
    order_clause = ""
    if "id" in cols:
        order_clause = " ORDER BY id DESC"
    elif "ts" in cols:
        order_clause = " ORDER BY ts DESC NULLS LAST"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT dataset_id FROM {table_name} WHERE dataset_id IS NOT NULL{order_clause} LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None


def _latest_event_dataset_id(entity_type: str = "crash") -> Optional[str]:
    uid = get_active_user()
    if not uid:
        return None
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_id
                FROM """ + APP_DATASETS + """
                WHERE owner_user_id=%s AND status='ready' AND entity_type=%s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (uid, entity_type),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _get_event_schema_columns(dataset_id: str) -> set[str]:
    cols: set[str] = set()
    uid = get_active_user()
    if not uid or not dataset_id:
        return cols
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT key
                FROM (
                    SELECT jsonb_object_keys(props) AS key
                    FROM """ + APP_EVENTS + """
                    WHERE dataset_id=%s AND owner_user_id=%s
                    LIMIT 2000
                ) t
                """,
                (dataset_id, uid),
            )
            cols = {str(r[0]) for r in cur.fetchall() if r and r[0]}
    except Exception:
        cols = set()
    return cols


def _parse_loose_json(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return None


def _extract_workzone_lines(geometry_value: Any) -> list[list[list[float]]]:
    geometry = _parse_loose_json(geometry_value)
    if not isinstance(geometry, dict):
        return []
    coords = geometry.get("coordinates")
    if not coords:
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [coords]
    if geometry_type == "MultiLineString":
        return list(coords)
    return []


def list_available_columns(dataset: str = "crash") -> str:
    """List available columns in the latest crash/event dataset for this session."""
    try:
        entity_type = "crash" if dataset.lower() in {"crash", "crashes"} else "event"
        dataset_id = _latest_event_dataset_id(entity_type=entity_type)
        if not dataset_id:
            return "No dataset found. Please upload a crash/event dataset first."

        schema_cols = _get_event_schema_columns(dataset_id)
        if not schema_cols:
            return f"No columns found in dataset {dataset_id}"

        profiles: dict[str, dict] = {}
        try:
            col_data = postgis_store.get_dataset_columns(dataset_id)
            profiles = col_data.get("column_profiles", {})
        except Exception:
            profiles = {}

        result_lines = [
            f"Dataset: {dataset_id}",
            f"Total columns: {len(schema_cols)}",
            "",
            "Available columns:",
        ]

        categorical_cols = []
        numeric_cols = []
        date_cols = []
        other_cols = []

        for col in sorted(schema_cols):
            profile = profiles.get(col, {})
            dtype_cat = profile.get("dtype_category", "unknown")
            unique_vals = profile.get("unique_values")
            sample_vals = profile.get("sample_values", [])

            col_info = f"  - {col}"
            if unique_vals:
                vals_str = ", ".join(str(v) for v in unique_vals[:5])
                if len(unique_vals) > 5:
                    vals_str += f" (+{len(unique_vals)-5} more)"
                col_info += f" [{vals_str}]"
            elif sample_vals:
                col_info += f" (e.g., {sample_vals[0]})"

            col_lower = col.lower()
            if any(key in col_lower for key in ("date", "time", "timestamp")):
                date_cols.append(col_info)
            elif dtype_cat == "numeric" or any(
                key in col_lower for key in ("count", "num", "number", "killed", "injured", "age", "speed")
            ):
                numeric_cols.append(col_info)
            elif unique_vals and len(unique_vals) <= 10:
                categorical_cols.append(col_info)
            else:
                other_cols.append(col_info)

        if categorical_cols:
            result_lines.append("\n-- Categorical (good for filtering/grouping):")
            result_lines.extend(categorical_cols)
        if numeric_cols:
            result_lines.append("\n-- Numeric (good for aggregation/filtering):")
            result_lines.extend(numeric_cols)
        if date_cols:
            result_lines.append("\n-- Date/Time:")
            result_lines.extend(date_cols)
        if other_cols:
            result_lines.append("\n-- Other:")
            result_lines.extend(other_cols[:20])
            if len(other_cols) > 20:
                result_lines.append(f"  ... and {len(other_cols)-20} more")

        suggestions = generate_query_suggestions(schema_cols, profiles)
        if suggestions:
            result_lines.append("\n## Suggested Queries:")
            for idx, suggestion in enumerate(suggestions[:5], 1):
                result_lines.append(f"  {idx}. {suggestion}")

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error getting columns: {str(e)}"


def list_session_datasets() -> list[dict]:
    """List all datasets uploaded in the current session."""
    uid = get_active_user() or "dev-user"
    rows: list[dict] = []

    try:
        with _db_conn() as conn:
            if _relation_exists(conn, APP_DATASETS):
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT dataset_id, name, entity_type, status, created_at,
                               COALESCE(stats->'ingest'->>'rows_inserted', stats->>'rows_inserted') as row_count
                        FROM """ + APP_DATASETS + """
                        WHERE owner_user_id = %s AND status = 'ready'
                        ORDER BY created_at DESC
                        """,
                        (uid,),
                    )
                    rows = [dict(r) for r in cur.fetchall()]

            if _relation_exists(conn, "public.cv_runs"):
                active_run_id = get_active_cv_run_id(uid) or ""

                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT run_id, schema_name, state_code, point_count, ts_start, ts_end,
                               created_at, is_visible, display_name
                        FROM public.cv_runs
                        ORDER BY created_at DESC
                        LIMIT 25
                        """
                    )
                    for run in cur.fetchall() or []:
                        run = dict(run)
                        run_id = str(run.get("run_id") or "").strip()
                        if not run_id:
                            continue
                        rows.append(
                            {
                                "dataset_id": f"cv_run:{run_id}",
                                "name": run.get("display_name") or run_id,
                                "entity_type": "cv",
                                "status": "ready" if (run.get("is_visible") is not False) else "hidden",
                                "created_at": run.get("created_at"),
                                "row_count": run.get("point_count"),
                                "source": "cv_runs",
                                "run_id": run_id,
                                "schema_name": run.get("schema_name"),
                                "state_code": run.get("state_code"),
                                "ts_start": run.get("ts_start"),
                                "ts_end": run.get("ts_end"),
                                "is_active": bool(active_run_id and run_id == active_run_id),
                            }
                        )
    except Exception:
        pass

    has_cv = any(r.get("entity_type") == "cv" and r.get("source") != "cv_runs" for r in rows)
    if not has_cv:
        try:
            with _db_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT dataset_id FROM cv_points ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    cv_id = row[0]
                    cur.execute("SELECT COUNT(*) FROM cv_points WHERE dataset_id=%s", (cv_id,))
                    cv_count = int(cur.fetchone()[0])
                    rows.append(
                        {
                            "dataset_id": cv_id,
                            "name": "traffic",
                            "entity_type": "cv",
                            "status": "ready",
                            "created_at": None,
                            "row_count": cv_count,
                            "stats": {
                                "ingest": {
                                    "column_profiles": {
                                        "lat": {},
                                        "lon": {},
                                        "ts": {},
                                        "road_segment_id": {},
                                        "speed": {},
                                        "SpeedLimitMPH": {},
                                        "speed_over_limit": {},
                                        "acc_x": {},
                                        "acc_y": {},
                                        "road": {},
                                    }
                                }
                            },
                        }
                    )
        except Exception:
            pass

    has_hard_braking = any((r.get("entity_type") or "").lower() == "hard_braking" for r in rows)
    if not has_hard_braking:
        try:
            with _db_conn() as conn:
                hb_table = _resolve_hard_brake_table(conn)
                if hb_table:
                    hb_dataset_id = _latest_dataset_id_from_relation(conn, hb_table)
                    if hb_dataset_id:
                        with conn.cursor() as cur:
                            cur.execute(f"SELECT COUNT(*) FROM {hb_table} WHERE dataset_id = %s", (hb_dataset_id,))
                            hb_count = int(cur.fetchone()[0])
                        rows.append(
                            {
                                "dataset_id": hb_dataset_id,
                                "name": "hard_braking",
                                "entity_type": "hard_braking",
                                "status": "ready",
                                "created_at": None,
                                "row_count": hb_count,
                                "source": "hard_brake_table",
                                "table": hb_table,
                            }
                        )
        except Exception:
            pass

    return rows


def show_datasets_on_map(dataset_names: list[str], limit_per_dataset: int = 2000) -> str:
    """Generate a combined map for any list of uploaded datasets."""
    log = logging.getLogger("adk_server")
    uid = get_active_user() or "dev-user"

    from .conflation_service import get_dataset_info, is_hard_braking_ref, resolve_dataset_id_generic

    results = []
    all_points = []
    all_lines = []

    for name in dataset_names:
        try:
            if is_hard_braking_ref(name):
                with _db_conn() as conn:
                    hb_table = _resolve_hard_brake_table(conn)
                    if not hb_table:
                        raise ValueError("Hard-braking table is not available.")
                    dataset_id = _latest_dataset_id_from_relation(conn, hb_table)
                    if not dataset_id:
                        raise ValueError("No hard-braking dataset_id found in hard-braking table.")
                info = {
                    "dataset_id": dataset_id,
                    "name": "hard_braking",
                    "entity_type": "hard_braking",
                    "table": hb_table,
                    "geo_cols": {"lat": "lat", "lon": "lon", "geom_m": "geom_m"},
                    "time_col": "ts",
                    "props_col": "attrs",
                    "id_col": "id",
                    "has_date_range": False,
                }
            else:
                dataset_id = resolve_dataset_id_generic(name)
                info = get_dataset_info(dataset_id)
                dataset_id = info.get("dataset_id", dataset_id)

            entity_type = info.get("entity_type", "unknown")
            table = info["table"]
            props_col = info["props_col"]

            if entity_type == "workzone":
                with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"""
                        SELECT id, lat, lon, road_segment_id, {props_col} as props
                        FROM {table}
                        WHERE dataset_id = %s AND owner_user_id = %s
                        LIMIT %s
                        """,
                        (dataset_id, uid, limit_per_dataset),
                    )
                    rows = cur.fetchall()

                for row in rows:
                    props = row.get("props") or {}
                    if isinstance(props, str):
                        try:
                            props = json.loads(props)
                        except Exception:
                            props = {}

                    geometry = props.get("geometry")
                    if geometry:
                        line_sets = _extract_workzone_lines(geometry)
                        core = props.get("core_details") or {}
                        if isinstance(core, str):
                            try:
                                core = json.loads(core)
                            except Exception:
                                core = {}

                        road_name = props.get("road_name")
                        if not road_name:
                            road_names = core.get("road_names")
                            if isinstance(road_names, list) and road_names:
                                road_name = " / ".join([str(v) for v in road_names if v])

                        for line_idx, coords in enumerate(line_sets):
                            clean_coords = []
                            for pair in coords:
                                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                                    try:
                                        clean_coords.append([float(pair[0]), float(pair[1])])
                                    except Exception:
                                        continue
                            if len(clean_coords) >= 2:
                                all_lines.append(
                                    {
                                        "id": f"{dataset_id}-{row['id']}-{line_idx}",
                                        "coordinates": clean_coords,
                                        "roadName": road_name or "Unknown",
                                        "datasetType": entity_type,
                                        "datasetName": info.get("name", name),
                                        "status": props.get("vehicle_impact") or props.get("status"),
                                    }
                                )
                    elif row.get("lat") and row.get("lon"):
                        all_points.append(
                            {
                                "latitude": float(row["lat"]),
                                "longitude": float(row["lon"]),
                                "type": entity_type.title(),
                                "datasetType": entity_type,
                                "datasetName": info.get("name", name),
                            }
                        )

                results.append(f"- {info.get('name', name)}: {len(rows)} {entity_type}s")
            else:
                with _db_conn() as conn:
                    table_cols = _table_column_names(conn, table)
                    alias = "t"
                    where_parts: list[str] = []
                    params: list[Any] = []
                    if "dataset_id" in table_cols:
                        where_parts.append(f"{alias}.dataset_id = %s")
                        params.append(dataset_id)
                    if "owner_user_id" in table_cols:
                        where_parts.append(f"{alias}.owner_user_id = %s")
                        params.append(uid)
                    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                    random_sql = "ORDER BY RANDOM()" if table == "cv_points" else ""
                    sql = f"""
                        SELECT {alias}.id, {alias}.lat, {alias}.lon, {alias}.ts,
                               {alias}.road_segment_id, {alias}.{props_col} as props,
                               COALESCE(
                                   NULLIF({alias}.{props_col}->>'road_name',''),
                                   NULLIF({alias}.{props_col}->>'road',''),
                                   NULLIF({alias}.{props_col}->>'RoadName',''),
                                   NULLIF({alias}.{props_col}->>'roadName',''),
                                   r.name,
                                   NULLIF({alias}.road_segment_id,'')
                               ) AS resolved_road_name,
                               COALESCE(
                                   NULLIF({alias}.{props_col}->>'speed',''),
                                   NULLIF({alias}.{props_col}->>'SpeedMPH',''),
                                   NULLIF({alias}.{props_col}->>'speed_mph',''),
                                   NULLIF({alias}.{props_col}->>'speedMPH','')
                               ) AS resolved_speed,
                               COALESCE(
                                   NULLIF({alias}.{props_col}->>'SpeedLimitMPH',''),
                                   NULLIF({alias}.{props_col}->>'speedLimit',''),
                                   NULLIF({alias}.{props_col}->>'speed_limit_mph',''),
                                   NULLIF({alias}.{props_col}->>'speed_limit',''),
                                   NULLIF({alias}.{props_col}->>'at_loc_speed_limit','')
                               ) AS resolved_speed_limit
                        FROM {table} {alias}
                        LEFT JOIN roads r ON r.road_segment_id = {alias}.road_segment_id
                        {where_sql}
                        {random_sql}
                        LIMIT %s
                    """
                    params.append(limit_per_dataset)
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(sql, params)
                        rows = cur.fetchall()

                for row in rows:
                    if row.get("lat") and row.get("lon"):
                        props = row.get("props") or {}
                        if isinstance(props, str):
                            try:
                                props = json.loads(props)
                            except Exception:
                                props = {}

                        if entity_type == "cv":
                            point_type = "Traffic"
                            point_label = "Vehicle"
                        elif entity_type == "hard_braking":
                            point_type = "HardBrake"
                            point_label = "HardBrake"
                        else:
                            point_type = entity_type
                            point_label = entity_type.title()

                        road_name = row.get("resolved_road_name") or ""
                        resolved_speed = row.get("resolved_speed")
                        resolved_speed_limit = row.get("resolved_speed_limit")

                        point = {
                            "latitude": float(row["lat"]),
                            "longitude": float(row["lon"]),
                            "type": point_label,
                            "point_type": point_type,
                            "datasetType": entity_type,
                            "datasetName": info.get("name", name),
                            "roadName": road_name or "Unknown",
                            "road_name": road_name or "Unknown",
                        }

                        if resolved_speed is not None:
                            try:
                                point["speed"] = float(resolved_speed)
                            except (TypeError, ValueError):
                                pass
                        if resolved_speed_limit is not None:
                            try:
                                point["SpeedLimitMPH"] = float(resolved_speed_limit)
                            except (TypeError, ValueError):
                                pass

                        for key in ["severity", "description"]:
                            if key in props and props[key]:
                                point[key] = props[key]

                        all_points.append(point)

                results.append(f"- {info.get('name', name)}: {len(rows)} {entity_type}s")
        except Exception as e:
            log.warning("Could not map dataset '%s': %s", name, e)
            results.append(f"- {name}: ERROR - {str(e)}")

    if all_points or all_lines:
        map_payload = {
            "label": f"Combined Map ({len(all_points)} points, {len(all_lines)} lines)",
            "count": len(all_points) + len(all_lines),
        }
        if all_points:
            map_payload["points"] = all_points
        if all_lines:
            map_payload["lines"] = all_lines
        save_map_for_session(map_payload, map_type="combined")

    response = ["**Datasets mapped:**", ""] + results
    if all_points or all_lines:
        response.append("")
        response.append(f"Combined map generated with {len(all_points)} points and {len(all_lines)} lines.")

    return "\n".join(response)

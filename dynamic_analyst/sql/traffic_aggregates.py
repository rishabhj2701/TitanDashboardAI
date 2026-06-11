"""Traffic SQL aggregate/optimizer helpers."""

from __future__ import annotations

import re

from .traffic_filters import _parse_agg_specs
from .common import (
    Any,
    DRIVABLE_HIGHWAY_TAGS,
    List,
    Optional,
    RealDictCursor,
    TRAFFIC_MAP_LIMIT_MAX,
    TRAFFIC_RESULT_LIMIT_WITH_MAP,
    TRAFFIC_SQL_RESULT_TIMEOUT_MS,
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _compile_filter,
    _cv_relation_candidates,
    _db_conn,
    _df_to_markdown_safe,
    _first_existing_relation,
    _make_map_payload,
    _publish_chart_payload,
    _query_requests_chart,
    _table_column_names,
    json,
    pd,
    save_map_for_session,
)

def _table_has_column(conn, table_name: str, column: str) -> bool:
    with conn.cursor() as cur:
        # Works for tables and materialized views.
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


def _resolve_road_stats_table(conn) -> Optional[str]:
    candidates: list[str] = []
    candidates.extend(_cv_relation_candidates(conn, "cv_road_stats_mv"))
    candidates.extend(_cv_relation_candidates(conn, "cv_road_segment_stats"))
    candidates.extend(_cv_relation_candidates(conn, "cv_road_agg"))
    candidates.extend(
        [
            "viz_matched_roads_tbl",
            "public.viz_matched_roads_tbl",
            "mm_rawmatch.cv_road_segment_stats",
            "mm_rawmatch.cv_road_agg",
        ]
    )
    candidates = list(dict.fromkeys(candidates))
    with conn.cursor() as cur:
        for name in candidates:
            cur.execute("SELECT to_regclass(%s)", (name,))
            row = cur.fetchone()
            if row and row[0]:
                return name
    return None


def _pick_column(conn, table_name: str, options: list[str]) -> Optional[str]:
    for opt in options:
        if _table_has_column(conn, table_name, opt):
            return opt
    return None



def _table_highway_uses_osm_tags(conn, table: str, highway_col: Optional[str]) -> bool:
    """True when the table stores OSM-style highway tags (motorway, primary, ...)."""
    if not table or not highway_col or not re.match(r"^[A-Za-z0-9_]+$", str(highway_col)):
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1
                FROM {table}
                WHERE LOWER(TRIM(COALESCE({highway_col}::text, ''))) = ANY(%s)
                LIMIT 1
                """,
                (list(DRIVABLE_HIGHWAY_TAGS),),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _append_drivable_highway_clause(
    where_parts: List[str],
    params: List[Any],
    highway_expr: Optional[str],
    *,
    apply_drivable_highway_default: bool,
    conn=None,
    stats_table: Optional[str] = None,
    highway_col: Optional[str] = None,
) -> None:
    if not apply_drivable_highway_default or not highway_expr:
        return
    if conn is not None and stats_table:
        col = highway_col or highway_expr
        if not _table_highway_uses_osm_tags(conn, stats_table, col):
            return
    where_parts.append(f"LOWER(COALESCE({highway_expr}::text, '')) = ANY(%s)")
    params.append(list(DRIVABLE_HIGHWAY_TAGS))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_top_hard_brake_group_by(value: Any) -> str:
    group_by = str(value or "segment").strip().lower()
    if group_by in {"segment", "road_segment", "road_segment_id", "way", "way_id"}:
        return "segment"
    if group_by in {"road", "road_name", "name"}:
        return "road_name"
    if group_by in {"ref", "route", "road_ref", "highway_ref"}:
        return "ref"
    return "segment"


def _geojson_to_line_coords(geom_json: Any) -> list[list[float]]:
    """Convert GeoJSON geometry to [lon, lat] pairs for map line overlays."""
    if not geom_json:
        return []
    try:
        payload = json.loads(geom_json) if isinstance(geom_json, str) else geom_json
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    def _ring_coords(ring: list) -> list[list[float]]:
        out: list[list[float]] = []
        for pair in ring or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            lon, lat = float(pair[0]), float(pair[1])
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                continue
            out.append([lon, lat])
        return out

    gtype = str(payload.get("type") or "").strip()
    coords = payload.get("coordinates")
    if gtype == "LineString" and isinstance(coords, list):
        return _ring_coords(coords)
    if gtype == "MultiLineString" and isinstance(coords, list):
        merged: list[list[float]] = []
        for line in coords:
            merged.extend(_ring_coords(line))
        return merged
    if gtype == "Polygon" and isinstance(coords, list) and coords:
        return _ring_coords(coords[0])
    return []


def _resolve_road_ids_for_names(road_names: list[str], *, limit: int = 40) -> tuple[list[str], list[str]]:
    """Look up way_id / ref for road labels (for map tile + API filters)."""
    names = [str(n).strip() for n in (road_names or []) if str(n or "").strip()]
    if not names:
        return [], []
    segment_ids: list[str] = []
    refs: list[str] = []
    try:
        with _db_conn() as conn:
            table = _resolve_road_stats_table(conn)
            if not table:
                return [], []
            cols = set(_table_column_names(conn, table))
            name_col = next((c for c in ("label", "road_name", "name", "ref") if c in cols), None)
            if not name_col:
                return [], []
            seg_col = next((c for c in ("way_id", "road_segment_id", "segment_id") if c in cols), None)
            ref_col = "ref" if "ref" in cols else None
            if not seg_col:
                return [], []

            clauses: list[str] = []
            params: list[Any] = []
            for name in names[:limit]:
                clauses.append(f"{name_col}::text ILIKE %s")
                params.append(f"%{name}%")
            where_sql = " OR ".join(clauses) if clauses else "FALSE"

            seg_select = f"{seg_col}::text" if seg_col else "NULL::text"
            ref_select = f"{ref_col}::text" if ref_col else "NULL::text"
            sql = f"""
                SELECT DISTINCT {seg_select} AS road_segment_id, {ref_select} AS road_ref
                FROM {table}
                WHERE ({where_sql})
                LIMIT %s
            """
            params.append(int(limit))
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                for row in cur.fetchall() or []:
                    if not isinstance(row, dict):
                        continue
                    seg = str(row.get("road_segment_id") or "").strip()
                    ref = str(row.get("road_ref") or "").strip()
                    if seg and seg not in segment_ids:
                        segment_ids.append(seg)
                    if ref and ref not in refs:
                        refs.append(ref)
    except Exception:
        return [], []
    return segment_ids[:limit], refs[:limit]


def _fetch_road_highlight_geometries(road_names: list[str], *, limit: int = 40) -> tuple[list[dict], list[dict]]:
    """Load line + marker geometry for ranked/filtered roads from cv_road_agg."""
    names = [str(n).strip() for n in (road_names or []) if str(n or "").strip()]
    if not names:
        return [], []

    lines: list[dict] = []
    points: list[dict] = []
    try:
        with _db_conn() as conn:
            table = _resolve_road_stats_table(conn)
            if not table:
                return [], []

            cols = set(_table_column_names(conn, table))
            geom_col = next((c for c in ("geom_4326", "geom_3857", "geom", "geometry") if c in cols), None)
            if not geom_col:
                return [], []
            geom_expr = "ST_Transform(geom_3857, 4326)" if geom_col == "geom_3857" else geom_col
            name_col = next((c for c in ("label", "road_name", "name", "ref") if c in cols), None)
            if not name_col:
                return [], []
            seg_col = next((c for c in ("road_segment_id", "way_id", "segment_id") if c in cols), None)

            clauses: list[str] = []
            params: list[Any] = []
            for name in names[:limit]:
                clauses.append(f"{name_col}::text ILIKE %s")
                params.append(f"%{name}%")
            where_sql = " OR ".join(clauses) if clauses else "FALSE"

            seg_select = f"{seg_col}::text" if seg_col else "NULL::text"
            sql = f"""
                SELECT
                    {name_col}::text AS road_name,
                    {seg_select} AS road_segment_id,
                    ST_AsGeoJSON({geom_expr}) AS geom_json,
                    ST_Y(ST_LineInterpolatePoint(ST_LineMerge({geom_expr}), 0.5)) AS latitude,
                    ST_X(ST_LineInterpolatePoint(ST_LineMerge({geom_expr}), 0.5)) AS longitude
                FROM {table}
                WHERE {geom_col} IS NOT NULL
                  AND ({where_sql})
                LIMIT %s
            """
            params.append(int(limit))
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() or []
    except Exception:
        return [], []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        coords = _geojson_to_line_coords(row.get("geom_json"))
        road_name = str(row.get("road_name") or "").strip() or "Road"
        seg_id = str(row.get("road_segment_id") or "").strip() or None
        if coords:
            lines.append(
                {
                    "id": f"road-line-{idx}",
                    "coordinates": coords,
                    "roadName": road_name,
                    "roadSegmentId": seg_id,
                    "exclusive": False,
                }
            )
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is not None and lon is not None:
            points.append(
                {
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "type": "HardBrake",
                    "point_type": "HardBrake",
                    "road_name": road_name,
                    "roadName": road_name,
                    "road_segment_id": seg_id,
                }
            )
    return lines, points


def _fetch_segment_geometries_by_ids(
    segment_ids: list[str],
    *,
    limit: int = 40,
) -> tuple[list[dict], list[dict]]:
    """Load RAMS/CV segment linework by way_id (state + county segments in cv_road_stats_mv)."""
    ids = [str(v).strip() for v in (segment_ids or []) if str(v or "").strip()]
    if not ids:
        return [], []

    lines: list[dict] = []
    points: list[dict] = []
    try:
        with _db_conn() as conn:
            table = _resolve_road_stats_table(conn)
            if not table:
                return [], []

            cols = set(_table_column_names(conn, table))
            geom_col = next((c for c in ("geom_4326", "geom_3857", "geom", "geometry") if c in cols), None)
            if not geom_col:
                return [], []
            geom_expr = "ST_Transform(geom_3857, 4326)" if geom_col == "geom_3857" else geom_col
            name_col = next((c for c in ("label", "road_name", "name", "ref") if c in cols), None)
            seg_col = next((c for c in ("way_id", "road_segment_id", "segment_id") if c in cols), None)
            avg_col = next((c for c in ("avg_speed_mph", "avg_speed") if c in cols), None)
            limit_col = next((c for c in ("speed_limit_mph", "speed_limit_mode") if c in cols), None)
            if not name_col or not seg_col:
                return [], []

            seg_select = f"{seg_col}::text"
            avg_select = f"{avg_col}::float8 AS avg_speed_mph" if avg_col else "NULL::float8 AS avg_speed_mph"
            limit_select = (
                f"{limit_col}::float8 AS speed_limit_mph" if limit_col else "NULL::float8 AS speed_limit_mph"
            )
            sql = f"""
                SELECT
                    {name_col}::text AS road_name,
                    {seg_select} AS road_segment_id,
                    {avg_select},
                    {limit_select},
                    ST_AsGeoJSON({geom_expr}) AS geom_json,
                    ST_Y(ST_LineInterpolatePoint(ST_LineMerge({geom_expr}), 0.5)) AS latitude,
                    ST_X(ST_LineInterpolatePoint(ST_LineMerge({geom_expr}), 0.5)) AS longitude
                FROM {table}
                WHERE {geom_col} IS NOT NULL
                  AND {seg_select} = ANY(%s)
                LIMIT %s
            """
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, [ids[:limit], int(limit)])
                rows = cur.fetchall() or []
    except Exception:
        return [], []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        coords = _geojson_to_line_coords(row.get("geom_json"))
        road_name = str(row.get("road_name") or "").strip() or "Road"
        seg_id = str(row.get("road_segment_id") or "").strip() or None
        if coords:
            line_props: dict[str, Any] = {
                "id": f"crash-seg-line-{idx}",
                "coordinates": coords,
                "roadName": road_name,
                "roadSegmentId": seg_id,
                "lineKind": "segment",
                "exclusive": False,
            }
            avg_val = row.get("avg_speed_mph")
            if avg_val is not None and pd.notna(avg_val):
                line_props["avg_speed_mph"] = float(avg_val)
            limit_val = row.get("speed_limit_mph")
            if limit_val is not None and pd.notna(limit_val):
                line_props["speed_limit_mph"] = float(limit_val)
            lines.append(line_props)
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is not None and lon is not None:
            points.append(
                {
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "type": "Road",
                    "point_type": "Road",
                    "road_name": road_name,
                    "roadName": road_name,
                    "road_segment_id": seg_id,
                }
            )
    return lines, points


def _save_road_aggregate_filter_from_df(
    df_in: pd.DataFrame,
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    road_filter_name: Optional[str],
    road_filter_segment: Optional[str],
    head_req: Optional[int],
    group_cols_in: Optional[list[str]] = None,
) -> None:
    try:
        if df_in is None or df_in.empty:
            return

        candidate_cols: list[str] = []
        for c in (group_cols_in or []):
            if isinstance(c, str) and c:
                candidate_cols.append(c)
        candidate_cols.extend(["road", "road_name", "name", "label", "ref", "highway"])

        available_cols: list[str] = []
        for c in candidate_cols:
            if c in df_in.columns and c not in available_cols:
                available_cols.append(c)

        road_names: list[str] = []
        for col in available_cols:
            for raw in df_in[col].tolist():
                if raw is None:
                    continue
                value = str(raw).strip()
                if not value:
                    continue
                if value.lower() in {"[unknown]", "[no_name]", "unknown", "none", "nan", "null"}:
                    continue
                road_names.append(value)

        deduped_names: list[str] = []
        seen: set[str] = set()
        for name in road_names:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_names.append(name)
            if len(deduped_names) >= 40:
                break

        if not deduped_names and not road_filter_name and not road_filter_segment:
            return

        min_points_for_map = 140
        if deduped_names:
            min_points_for_map = 1
            point_count_col = next(
                (c for c in ("point_count", "count", "points") if c in df_in.columns),
                None,
            )
            if point_count_col:
                try:
                    vals = pd.to_numeric(df_in[point_count_col], errors="coerce").dropna()
                    if not vals.empty:
                        min_points_for_map = max(1, int(vals.min()))
                except Exception:
                    min_points_for_map = 1

        lookup_names = list(deduped_names)
        if road_filter_name and road_filter_name not in lookup_names:
            lookup_names.insert(0, road_filter_name)
        segment_ids, road_refs = _resolve_road_ids_for_names(lookup_names, limit=40)
        if "road_segment_id" in df_in.columns:
            for raw in df_in["road_segment_id"].tolist():
                if raw is None:
                    continue
                seg = str(raw).strip()
                if seg and seg not in segment_ids:
                    segment_ids.append(seg)
                if len(segment_ids) >= 40:
                    break

        payload_filter: dict[str, Any] = {
            "road_name": road_filter_name or (deduped_names[0] if len(deduped_names) == 1 else None),
            "road_segment_id": road_filter_segment or (segment_ids[0] if len(segment_ids) == 1 else None),
            "min_points": min_points_for_map,
        }
        if deduped_names:
            payload_filter["road_names"] = deduped_names
            payload_filter["limit"] = min(len(deduped_names), 40)
        elif head_req is not None:
            payload_filter["limit"] = int(head_req)
        if segment_ids:
            payload_filter["road_segment_ids"] = segment_ids[:40]
        if road_refs:
            payload_filter["road_refs"] = road_refs

        label = "Traffic Road Aggregate"
        if deduped_names:
            preview = ", ".join(deduped_names[:3])
            if len(deduped_names) > 3:
                preview += f" (+{len(deduped_names) - 3} more)"
            label = f"Roads: {preview}"

        lines, marker_points = _fetch_road_highlight_geometries(deduped_names, limit=40)
        if segment_ids:
            seg_lines, seg_points = _fetch_segment_geometries_by_ids(segment_ids, limit=40)
            lines = seg_lines or lines
            if not marker_points:
                marker_points = seg_points
        map_payload: dict[str, Any] = {
            "label": label,
            "count": len(marker_points),
            "points": marker_points,
            "lines": lines,
            "roadAggregateFilter": payload_filter,
            "overlay": bool(marker_points or lines),
            "analysis_type": "traffic_roads",
        }
        save_map_for_session(map_payload, map_type="traffic")
        log.info(
            json.dumps(
                {
                    "event": "road_aggregate_map_filter",
                    "dataset": dataset,
                    "dataset_id": dataset_id,
                    "filter": payload_filter,
                },
                default=str,
            )
        )
    except Exception as exc:
        log.debug("road aggregate map filter save skipped: %s", exc)


def _try_run_road_stats_optimized(
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
    hard_brake_only: bool,
    groupby_req: Optional[dict],
    aggregate_req: Optional[dict],
    sort_cols: list[str],
    sort_dirs: list[bool],
    head_req: Optional[int],
    map_req: Optional[dict],
    filters: list[dict],
    filter_mode: str,
    having_conditions: Optional[list] = None,
    having_mode: str = "and",
    col_map: dict[str, str] = None,
    reasoning: str = "",
    steps: list[dict] = None,
    apply_drivable_highway_default: bool = True,
    road_filter_name: Optional[str] = None,
    road_filter_segment: Optional[str] = None,
) -> Optional[str]:
    # Road-stats optimization is for road-level analytics only.
    if near_crash or near_workzone or hard_brake_only:
        return None
    if groupby_req is None and aggregate_req is None:
        return None

    try:
        with _db_conn() as conn:
            table = _resolve_road_stats_table(conn)
            if not table:
                return None

            road_col = _pick_column(conn, table, ["label", "road_name", "road", "name", "ref", "highway"])
            road_id_col = _pick_column(conn, table, ["road_segment_id", "way_id", "segment_id", "road_id"])
            points_col = _pick_column(conn, table, ["point_count", "points", "count"])
            speed_col = _pick_column(conn, table, ["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
            speed_limit_col = _pick_column(conn, table, ["speed_limit_mph", "avg_speed_limit_mph", "speed_limit_mode"])
            min_speed_col = _pick_column(conn, table, ["min_speed_mph", "min_speed"])
            max_speed_col = _pick_column(conn, table, ["max_speed_mph", "max_speed"])
            std_speed_col = _pick_column(conn, table, ["speed_stddev_mph", "stddev_speed_mph", "speed_stddev"])
            start_ts_col = _pick_column(conn, table, ["start_ts", "ts_start", "min_ts"])
            end_ts_col = _pick_column(conn, table, ["end_ts", "ts_end", "max_ts"])
            ref_col = _pick_column(conn, table, ["ref"])
            highway_col = _pick_column(conn, table, ["highway"])
            hard_brake_col = _pick_column(conn, table, ["hard_brake_count", "decel_03g_sum"])

            if not road_col or not points_col:
                return None

            road_expr = f"NULLIF({road_col}::text,'')"
            rs_col_map: dict[str, str] = {
                "road": road_expr,
                "road_name": road_expr,
                "name": road_expr,
                "point_count": f"{points_col}::float8",
                "points": f"{points_col}::float8",
            }
            if road_col:
                rs_col_map[road_col] = road_expr
            if ref_col:
                rs_col_map["ref"] = f"NULLIF({ref_col}::text,'')"
                rs_col_map["route_id"] = rs_col_map["ref"]
                rs_col_map["routeid"] = rs_col_map["ref"]
            if road_id_col:
                rs_col_map["road_segment_id"] = f"{road_id_col}::text"
                rs_col_map["way_id"] = f"{road_id_col}::text"
            if speed_col:
                rs_col_map["speed"] = f"{speed_col}::float8"
                rs_col_map["avg_speed_mph"] = f"{speed_col}::float8"
                rs_col_map["avg_speed"] = f"{speed_col}::float8"
            if speed_limit_col:
                rs_col_map["SpeedLimitMPH"] = f"{speed_limit_col}::float8"
                rs_col_map["speedLimit"] = f"{speed_limit_col}::float8"
                rs_col_map["avg_speed_limit_mph"] = f"{speed_limit_col}::float8"
            else:
                rs_col_map["SpeedLimitMPH"] = "NULL::float8"
                rs_col_map["speedLimit"] = "NULL::float8"
                rs_col_map["avg_speed_limit_mph"] = "NULL::float8"
            rs_col_map["speed_over_limit"] = f"(({rs_col_map.get('speed', 'NULL::float8')}) - ({rs_col_map['SpeedLimitMPH']}))"
            rs_col_map["avg_speed_over_limit"] = rs_col_map["speed_over_limit"]
            rs_col_map["speed_over_limit_avg"] = rs_col_map["speed_over_limit"]
            rs_col_map["speed_over_limit_mean"] = rs_col_map["speed_over_limit"]
            rs_col_map["mean_speed_over_limit"] = rs_col_map["speed_over_limit"]
            if min_speed_col:
                rs_col_map["min_speed_mph"] = f"{min_speed_col}::float8"
                rs_col_map["min_speed"] = f"{min_speed_col}::float8"
            if max_speed_col:
                rs_col_map["max_speed_mph"] = f"{max_speed_col}::float8"
                rs_col_map["max_speed"] = f"{max_speed_col}::float8"
            if std_speed_col:
                rs_col_map["speed_stddev_mph"] = f"{std_speed_col}::float8"
                rs_col_map["speed_stddev"] = f"{std_speed_col}::float8"
            if start_ts_col:
                rs_col_map["start_ts"] = start_ts_col
                rs_col_map["ts_start"] = start_ts_col
            if end_ts_col:
                rs_col_map["end_ts"] = end_ts_col
                rs_col_map["ts_end"] = end_ts_col
            if highway_col:
                rs_col_map["highway"] = f"NULLIF({highway_col}::text,'')"
            if hard_brake_col:
                rs_col_map["hard_brake_count"] = f"COALESCE({hard_brake_col}::float8, 0)"
                rs_col_map["decel_03g_sum"] = rs_col_map["hard_brake_count"]

            where_parts_stats: list[str] = []
            params: list[Any] = []
            if dataset_id and _table_has_column(conn, table, "dataset_id"):
                where_parts_stats.append("dataset_id = %s")
                params.append(dataset_id)
            _append_drivable_highway_clause(
                where_parts_stats,
                params,
                highway_col,
                apply_drivable_highway_default=apply_drivable_highway_default,
                conn=conn,
                stats_table=table,
                highway_col=highway_col,
            )

            try:
                filt_clause, filt_params, _ = _compile_filter(filters, filter_mode, rs_col_map)
            except Exception:
                return None
            if filt_clause:
                where_parts_stats.append(f"({filt_clause})")
                params.extend(filt_params)

            where_stats = " AND ".join(where_parts_stats) if where_parts_stats else "TRUE"

            sql = ""
            group_cols_local: list[str] = []
            if groupby_req is not None:
                from .query_helpers import _groupby_columns_from_params

                group_cols_local = _groupby_columns_from_params(groupby_req)
                aggs = groupby_req.get("aggregations") or {}
                if not group_cols_local or not aggs:
                    return None

                gb_exprs: list[str] = []
                gb_selects: list[str] = []
                for c in group_cols_local:
                    if c not in rs_col_map:
                        return None
                    gb_exprs.append(rs_col_map[c])
                    gb_selects.append(f"{rs_col_map[c]} AS {c}")

                try:
                    agg_specs, agg_alias_map = _parse_agg_specs(
                        aggs,
                        default_col_map=col_map,
                        allowed_col_map=rs_col_map,
                    )
                except Exception:
                    return None
                agg_selects: list[str] = []
                agg_aliases: list[str] = []
                agg_expr_map: dict[str, str] = {}  # alias -> SQL aggregate expression (for HAVING)
                for col, fn, alias in agg_specs:
                    if col == "count":
                        sql_expr = f"SUM({points_col})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                        agg_aliases.append(alias)
                        agg_expr_map[alias] = sql_expr
                        continue
                    expr = rs_col_map[col]
                    if fn in ("mean", "avg"):
                        # Speed and speed_over_limit must be weighted by point_count to avoid
                        # treating short low-volume segments equally with high-traffic roads.
                        if col in ("speed", "speed_over_limit") and points_col:
                            speed_raw = rs_col_map.get("speed", f"{speed_col}::float8")
                            if col == "speed_over_limit" and speed_limit_col:
                                limit_raw = rs_col_map.get("SpeedLimitMPH", f"{speed_limit_col}::float8")
                                sql_expr = (
                                    f"((SUM({speed_raw} * {points_col}) - SUM({limit_raw} * {points_col}))"
                                    f" / NULLIF(SUM({points_col}), 0))"
                                )
                            else:
                                sql_expr = f"(SUM({speed_raw} * {points_col}) / NULLIF(SUM({points_col}), 0))"
                        else:
                            sql_expr = f"AVG({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "sum":
                        sql_expr = f"SUM({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "min":
                        sql_expr = f"MIN({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "max":
                        sql_expr = f"MAX({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    elif fn == "count":
                        if col in ("point_count", "points"):
                            sql_expr = f"SUM({points_col})"
                        else:
                            sql_expr = f"COUNT({expr})"
                        agg_selects.append(f"{sql_expr} AS {alias}")
                    else:
                        return None
                    agg_aliases.append(alias)
                    agg_expr_map[alias] = sql_expr

                order_clause = ""
                if sort_cols:
                    order_parts: list[str] = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        if col in group_cols_local:
                            order_expr = col
                        elif col in agg_aliases:
                            order_expr = col
                        elif col in agg_alias_map:
                            order_expr = agg_alias_map[col]
                        else:
                            return None
                        order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                    if order_parts:
                        order_clause = f" ORDER BY {', '.join(order_parts)}"

                # Build HAVING clause using SQL expressions (not aliases) so PostgreSQL can evaluate them.
                having_clause = ""
                if having_conditions:
                    # Map alias -> actual SQL aggregate expression for use in HAVING
                    having_col_map = dict(agg_expr_map)
                    for k, v in agg_alias_map.items():
                        if v in agg_expr_map:
                            having_col_map[k] = agg_expr_map[v]
                    try:
                        hav_clause, hav_params, _ = _compile_filter(
                            having_conditions, having_mode or "and", having_col_map
                        )
                        if hav_clause:
                            having_clause = f" HAVING {hav_clause}"
                            params.extend(hav_params)
                    except Exception:
                        return None  # Fall through to the full SQL path.

                limit_clause = ""
                effective_head = head_req
                if effective_head is None and map_req is not None:
                    effective_head = TRAFFIC_RESULT_LIMIT_WITH_MAP
                if not order_clause.strip() and agg_aliases and effective_head is not None:
                    order_clause = f" ORDER BY {agg_aliases[0]} DESC NULLS LAST"
                if effective_head is not None:
                    limit_clause = " LIMIT %s"
                    params.append(int(effective_head))

                sql = f"""
                    SELECT {", ".join(gb_selects + agg_selects)}
                    FROM {table}
                    WHERE {where_stats}
                    GROUP BY {", ".join(gb_exprs)}
                    {having_clause}
                    {order_clause}
                    {limit_clause}
                """
            elif aggregate_req is not None:
                aggs = aggregate_req.get("aggregations") or {}
                if not aggs:
                    return None
                try:
                    agg_specs, agg_alias_map = _parse_agg_specs(
                        aggs,
                        default_col_map=col_map,
                        allowed_col_map=rs_col_map,
                    )
                except Exception:
                    return None
                agg_selects: list[str] = []
                agg_aliases: list[str] = []
                for col, fn, alias in agg_specs:
                    if col == "count":
                        agg_selects.append(f"SUM({points_col}) AS {alias}")
                        agg_aliases.append(alias)
                        continue
                    expr = rs_col_map[col]
                    if fn in ("mean", "avg"):
                        agg_selects.append(f"AVG({expr}) AS {alias}")
                    elif fn == "sum":
                        agg_selects.append(f"SUM({expr}) AS {alias}")
                    elif fn == "min":
                        agg_selects.append(f"MIN({expr}) AS {alias}")
                    elif fn == "max":
                        agg_selects.append(f"MAX({expr}) AS {alias}")
                    elif fn == "count":
                        if col in ("point_count", "points"):
                            agg_selects.append(f"SUM({points_col}) AS {alias}")
                        else:
                            agg_selects.append(f"COUNT({expr}) AS {alias}")
                    else:
                        return None
                    agg_aliases.append(alias)

                order_clause = ""
                if sort_cols:
                    order_parts: list[str] = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        if col in agg_aliases:
                            order_expr = col
                        elif col in agg_alias_map:
                            order_expr = agg_alias_map[col]
                        else:
                            return None
                        order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                    if order_parts:
                        order_clause = f" ORDER BY {', '.join(order_parts)}"

                limit_clause = ""
                if head_req is not None:
                    limit_clause = " LIMIT %s"
                    params.append(int(head_req))

                sql = f"""
                    SELECT {", ".join(agg_selects)}
                    FROM {table}
                    WHERE {where_stats}
                    {order_clause}
                    {limit_clause}
                """
            else:
                return None

            log.info(json.dumps({
                "event": "sql_compiled_road_stats_optimized",
                "dataset": dataset,
                "dataset_id": dataset_id,
                "sql": sql.strip()[:2500],
                "params": params,
            }, default=str))

            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (f"{TRAFFIC_SQL_RESULT_TIMEOUT_MS}ms",))
            df = pd.read_sql_query(sql, conn, params=params)

        df = _apply_codebook_labels(df, dataset_id=dataset_id)
        if groupby_req is not None:
            if any(c in ("road", "road_name", "name") for c in group_cols_local):
                _save_road_aggregate_filter_from_df(
                    df,
                    log=log,
                    dataset=dataset,
                    dataset_id=dataset_id,
                    road_filter_name=road_filter_name,
                    road_filter_segment=road_filter_segment,
                    head_req=head_req,
                    group_cols_in=group_cols_local,
                )
        elif map_req is not None:
            _save_road_aggregate_filter_from_df(
                df,
                log=log,
                dataset=dataset,
                dataset_id=dataset_id,
                road_filter_name=road_filter_name,
                road_filter_segment=road_filter_segment,
                head_req=head_req,
            )

        response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
        if reasoning:
            response_parts.append(f"Analysis Plan: {reasoning}\n")
        response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
        response_parts.append("Using pre-aggregated road statistics optimizer")
        if filters:
            response_parts.append(f"Filters applied: {filters}")
        if map_req is not None:
            response_parts.append("Map linkage: road lines derived from resulting road filter")
        if _query_requests_chart(reasoning, steps):
            auto_chart = _build_auto_chart_payload_from_df(
                df=df,
                reasoning=reasoning,
                steps=steps,
                chart_role="traffic_query_result",
                dataset_id=dataset_id or "__all__",
                group_cols=group_cols_local if groupby_req is not None else None,
            )
            if auto_chart:
                _publish_chart_payload([auto_chart], label="Traffic analysis chart")
                response_parts.append("Visualization generated in panel.")

        response_parts.append("\nFINAL DATA RESULTS:")
        if df.empty:
            response_parts.append("Result is an empty table (No data found).")
        else:
            response_parts.append(_df_to_markdown_safe(df))
        return "\n".join(response_parts)
    except Exception:
        return None


def _run_top_speed_roads_operation_impl(
    params: dict,
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    filters: list[dict],
    near_crash: Optional[dict],
    near_workzone: Optional[dict],
    hard_brake_only: bool,
    apply_drivable_highway_default: bool,
    col_map: dict[str, str],
    from_sql: str,
    where_sql: str,
    where_params: list[Any],
    lat_expr: str,
    lon_expr: str,
    reasoning: str,
    active_run_ctx: dict[str, Any],
) -> str:
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    min_points = _bounded_int(params.get("min_points"), default=120, minimum=1, maximum=1_000_000)
    hotspot_limit = _bounded_int(
        params.get("hotspot_limit"),
        default=0,
        minimum=0,
        maximum=TRAFFIC_MAP_LIMIT_MAX,
    )
    chart_requested = bool(
        params.get("generate_chart")
        or params.get("include_chart")
        or params.get("with_chart")
    )

    stats_attempted = False
    df = pd.DataFrame()
    if not filters and not near_crash and not near_workzone and not hard_brake_only:
        with _db_conn() as conn:
            stats_candidates: list[str] = []
            stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_stats_mv"))
            stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_agg"))
            stats_candidates.extend(["viz_matched_roads_tbl", "public.viz_matched_roads_tbl"])
            stats_table = _first_existing_relation(conn, list(dict.fromkeys(stats_candidates)))
            if stats_table:
                stats_cols = _table_column_names(conn, stats_table)

                def _pick_stat_col(options: list[str]) -> Optional[str]:
                    for col in options:
                        if col in stats_cols:
                            return col
                    return None

                road_col = _pick_stat_col(["label", "road_name", "road", "name", "ref", "highway"])
                road_id_col = _pick_stat_col(["road_segment_id", "way_id", "segment_id", "road_id"])
                speed_col = _pick_stat_col(["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
                points_col = _pick_stat_col(["point_count", "points", "count"])
                std_col = _pick_stat_col(["speed_stddev_mph", "stddev_speed_mph", "speed_stddev"])
                speed_limit_col = _pick_stat_col(["speed_limit_mph", "speed_limit_mode", "avg_speed_limit_mph"])
                start_col = _pick_stat_col(["start_ts", "ts_start", "min_ts"])
                end_col = _pick_stat_col(["end_ts", "ts_end", "max_ts"])
                highway_col = _pick_stat_col(["highway"])

                if road_col and speed_col and points_col:
                    stats_attempted = True
                    where_stats = [f"{road_col} IS NOT NULL", f"{speed_col} IS NOT NULL", f"{points_col} IS NOT NULL"]
                    stats_params: list[Any] = []
                    if dataset_id and "dataset_id" in stats_cols:
                        where_stats.append("dataset_id = %s")
                        stats_params.append(dataset_id)
                    _append_drivable_highway_clause(
                        where_stats,
                        stats_params,
                        highway_col,
                        apply_drivable_highway_default=apply_drivable_highway_default,
                        conn=conn,
                        stats_table=stats_table,
                        highway_col=highway_col,
                    )

                    road_id_select = f"MIN({road_id_col}::text)" if road_id_col else "NULL::text"
                    std_select = f"AVG({std_col})::float8" if std_col else "NULL::float8"
                    speed_limit_weighted = (
                        f"SUM({speed_limit_col} * {points_col}) / NULLIF(SUM({points_col}), 0)"
                        if speed_limit_col
                        else "NULL::float8"
                    )
                    start_select = f"MIN({start_col})" if start_col else "NULL::timestamptz"
                    end_select = f"MAX({end_col})" if end_col else "NULL::timestamptz"

                    stats_sql = f"""
                        WITH road_stats AS (
                            SELECT
                                {road_col} AS road_name,
                                {road_id_select} AS road_segment_id,
                                SUM({points_col})::bigint AS point_count,
                                (SUM({speed_col} * {points_col}) / NULLIF(SUM({points_col}), 0))::float8 AS avg_speed_mph,
                                {std_select} AS speed_stddev_mph,
                                ({speed_limit_weighted})::float8 AS avg_speed_limit_mph,
                                {start_select} AS start_ts,
                                {end_select} AS end_ts
                            FROM {stats_table}
                            WHERE {" AND ".join(where_stats)}
                            GROUP BY {road_col}
                            HAVING SUM({points_col}) >= %s
                        )
                        SELECT
                            ROW_NUMBER() OVER (ORDER BY avg_speed_mph DESC NULLS LAST, point_count DESC) AS rank,
                            road_name,
                            road_segment_id,
                            point_count,
                            avg_speed_mph,
                            speed_stddev_mph,
                            avg_speed_limit_mph,
                            (avg_speed_mph - avg_speed_limit_mph)::float8 AS avg_speed_over_limit_mph,
                            start_ts,
                            end_ts
                        FROM road_stats
                        ORDER BY rank
                        LIMIT %s
                    """
                    stats_params.extend([min_points, limit_n])
                    df = pd.read_sql_query(stats_sql, conn, params=stats_params)

    if not stats_attempted:
        rank_sql = f"""
            WITH filtered AS (
                SELECT
                    {col_map["road_name"]} AS road_name,
                    {col_map["road_segment_id"]} AS road_segment_id,
                    {col_map["speed"]} AS speed_mph,
                    {col_map["SpeedLimitMPH"]} AS speed_limit_mph,
                    {col_map["speed_over_limit"]} AS speed_over_limit_mph,
                    p.ts AS timestamp
                FROM {from_sql}
                WHERE {where_sql}
                  AND {col_map["road_name"]} IS NOT NULL
                  AND {col_map["speed"]} IS NOT NULL
                  {"AND LOWER(COALESCE(" + col_map["highway"] + "::text, '')) = ANY(%s)" if apply_drivable_highway_default and ("highway" in col_map) else ""}
            ),
            road_stats AS (
                SELECT
                    road_name,
                    MIN(road_segment_id)::text AS road_segment_id,
                    COUNT(*)::bigint AS point_count,
                    AVG(speed_mph)::float8 AS avg_speed_mph,
                    STDDEV_POP(speed_mph)::float8 AS speed_stddev_mph,
                    AVG(speed_limit_mph)::float8 AS avg_speed_limit_mph,
                    AVG(speed_over_limit_mph)::float8 AS avg_speed_over_limit_mph,
                    MIN(timestamp) AS start_ts,
                    MAX(timestamp) AS end_ts
                FROM filtered
                GROUP BY road_name
                HAVING COUNT(*) >= %s
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY avg_speed_mph DESC NULLS LAST, point_count DESC) AS rank,
                road_name,
                road_segment_id,
                point_count,
                avg_speed_mph,
                speed_stddev_mph,
                avg_speed_limit_mph,
                avg_speed_over_limit_mph,
                start_ts,
                end_ts
            FROM road_stats
            ORDER BY rank
            LIMIT %s
        """
        rank_params = list(where_params)
        if apply_drivable_highway_default and ("highway" in col_map):
            rank_params.append(list(DRIVABLE_HIGHWAY_TAGS))
        rank_params.extend([min_points, limit_n])
        with _db_conn() as conn:
            df = pd.read_sql_query(rank_sql, conn, params=rank_params)
    df = _apply_codebook_labels(df, dataset_id=dataset_id)

    top_road_names: list[str] = []
    top_segment_ids: list[str] = []
    if not df.empty and "road_name" in df.columns:
        seen_names: set[str] = set()
        for raw in df["road_name"].tolist():
            if raw is None:
                continue
            name = str(raw).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            top_road_names.append(name)
    if not df.empty and "road_segment_id" in df.columns:
        seen_segments: set[str] = set()
        for raw in df["road_segment_id"].tolist():
            if raw is None:
                continue
            seg = str(raw).strip()
            if not seg:
                continue
            if seg in seen_segments:
                continue
            seen_segments.add(seg)
            top_segment_ids.append(seg)

    if top_road_names and hotspot_limit > 0:
        hotspot_scope_clauses: list[str] = []
        hotspot_scope_params: list[Any] = []
        if top_segment_ids:
            hotspot_scope_clauses.append(f"({col_map['road_segment_id']})::text = ANY(%s::text[])")
            hotspot_scope_params.append(top_segment_ids)
        if top_road_names:
            hotspot_scope_clauses.append(f"LOWER({col_map['road_name']}) = ANY(%s::text[])")
            hotspot_scope_params.append([name.lower() for name in top_road_names])
        if not hotspot_scope_clauses:
            hotspot_rows = []
        else:
            hotspot_scope_sql = " OR ".join(hotspot_scope_clauses)
            hotspot_sql = f"""
                SELECT
                    {lat_expr} AS latitude,
                    {lon_expr} AS longitude,
                    p.ts AS timestamp,
                    {col_map["road_name"]} AS road_name,
                    {col_map["road_segment_id"]} AS road_segment_id,
                    {col_map["speed"]} AS speed,
                    {col_map["SpeedLimitMPH"]} AS "speedLimit",
                    {col_map["speed_over_limit"]} AS speed_over_limit,
                    {col_map["acc_x"]} AS acc_x,
                    {col_map["acc_y"]} AS acc_y
                FROM {from_sql}
                WHERE {where_sql}
                  AND {lat_expr} IS NOT NULL
                  AND {lon_expr} IS NOT NULL
                  AND ({hotspot_scope_sql})
                ORDER BY {col_map["speed_over_limit"]} DESC NULLS LAST, {col_map["speed"]} DESC NULLS LAST
                LIMIT %s
            """
            hotspot_params = list(where_params) + hotspot_scope_params + [hotspot_limit]
            with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(hotspot_sql, hotspot_params)
                hotspot_rows = cur.fetchall()
    else:
        hotspot_rows = []

    chart_payload: list[dict[str, Any]] = []
    if chart_requested and not df.empty:
        chart_df = df.copy()
        if "rank" in chart_df.columns:
            chart_df = chart_df.sort_values("rank", ascending=True)
        chart_df = chart_df.head(limit_n)

        road_labels: list[str] = []
        for idx, raw in enumerate(chart_df.get("road_name", pd.Series(dtype=object)).tolist()):
            name = str(raw).strip() if raw is not None else ""
            if not name:
                name = f"Road {idx + 1}"
            road_labels.append(name)

        avg_speed_values: list[Optional[float]] = []
        for raw in chart_df.get("avg_speed_mph", pd.Series(dtype=float)).tolist():
            avg_speed_values.append(float(raw) if pd.notna(raw) else None)

        point_counts: list[Optional[int]] = []
        for raw in chart_df.get("point_count", pd.Series(dtype=float)).tolist():
            point_counts.append(int(raw) if pd.notna(raw) else None)

        if road_labels and any(v is not None for v in avg_speed_values):
            chart_payload.append(
                {
                    "type": "bar",
                    "title": f"Top {len(road_labels)} Roads by Average Speed",
                    "xLabel": "Road",
                    "yLabel": "Average speed (mph)",
                    "xValues": road_labels,
                    "series": [
                        {
                            "label": "Avg speed (mph)",
                            "values": avg_speed_values,
                        },
                    ],
                    "orientation": "vertical",
                    "meta": {
                        "chartRole": "top_speed_roads",
                        "description": "Top-ranked roads by average speed for the active CV selection.",
                        "columnsUsed": [
                            "road_name",
                            "avg_speed_mph",
                            "point_count",
                        ],
                        "table": [
                            {
                                "road_name": road_labels[i],
                                "avg_speed_mph": avg_speed_values[i],
                                "point_count": point_counts[i],
                            }
                            for i in range(len(road_labels))
                        ],
                    },
                }
            )

    if top_road_names:
        map_label = f"Top {len(top_road_names)} roads by average speed"
        if hotspot_rows:
            map_label += " (hotspots)"
        map_payload = _make_map_payload(
            hotspot_rows,
            label=map_label,
            hard_brake_only=False,
        )
        road_filter_payload: dict[str, Any] = {
            "road_names": top_road_names[:40],
            "min_points": 1,
            "limit": min(len(top_road_names), 40),
        }
        if top_segment_ids:
            road_filter_payload["road_segment_ids"] = top_segment_ids[:40]
            if len(top_segment_ids) == 1:
                road_filter_payload["road_segment_id"] = top_segment_ids[0]
        map_payload["roadAggregateFilter"] = road_filter_payload
        map_payload["overlay"] = bool(hotspot_rows)
        map_payload["analysis_type"] = "top_speed_roads"
        if chart_payload:
            map_payload["chartPayload"] = chart_payload
        save_map_for_session(map_payload, map_type="traffic")
    elif chart_payload:
        save_map_for_session(
            {
                "label": "Top speed roads",
                "count": 0,
                "points": [],
                "analysis_type": "top_speed_roads",
                "chartPayload": chart_payload,
            },
            map_type="traffic",
        )

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    run_id = active_run_ctx.get("run_id") if isinstance(active_run_ctx, dict) else None
    if run_id:
        response_parts.append(f"Active CV run: {run_id}")
    state_code = (active_run_ctx.get("state_code") or "").strip() if isinstance(active_run_ctx, dict) else ""
    if state_code:
        response_parts.append(f"State scope: {state_code}")
    response_parts.append("Ranking metric: average speed (mph), descending.")
    response_parts.append(f"Minimum point threshold enforced: {min_points} points/road.")
    if top_road_names:
        if hotspot_rows:
            response_parts.append(
                f"Map output: highlighted {len(top_road_names)} ranked roads and {len(hotspot_rows)} hotspot points."
            )
        else:
            response_parts.append(
                f"Map output: highlighted {len(top_road_names)} ranked roads."
            )

    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no roads met the minimum point threshold).")
    else:
        response_parts.append(f"Top {min(limit_n, len(df))} roads by average speed:")
        for _, row in df.head(limit_n).iterrows():
            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
            road_name = str(row.get("road_name") or "Unknown road").strip()
            point_count = int(row.get("point_count")) if pd.notna(row.get("point_count")) else 0
            avg_speed = float(row.get("avg_speed_mph")) if pd.notna(row.get("avg_speed_mph")) else 0.0
            if rank is None:
                response_parts.append(f"- {road_name}: {avg_speed:.2f} mph ({point_count:,} points)")
            else:
                response_parts.append(f"{rank}. {road_name}: {avg_speed:.2f} mph ({point_count:,} points)")
    return "\n".join(response_parts)


def _run_roads_by_speed_limit_operation_impl(
    params: dict,
    *,
    log,
    dataset_id: Optional[str],
    reasoning: str,
) -> str:
    """List/highlight roads by posted speed limit or CV average speed (exact or range)."""
    limit_n = _bounded_int(params.get("limit"), default=500, minimum=1, maximum=2000)
    generate_map = bool(
        params.get("generate_map")
        or params.get("include_map")
        or params.get("with_map")
    )
    metric_raw = str(params.get("metric") or params.get("speed_metric") or "speed_limit").strip().lower()
    use_avg_speed = metric_raw in {"avg", "avg_speed", "average_speed", "average", "mean"}
    min_mph = params.get("speed_limit_min_mph")
    max_mph = params.get("speed_limit_max_mph")
    if min_mph is not None or max_mph is not None:
        low = float(min_mph if min_mph is not None else 0)
        high = float(max_mph if max_mph is not None else 200)
        if low > high:
            low, high = high, low
        target_mph = (low + high) / 2.0
        tolerance = None
        label_mph = f"{low:g}–{high:g}"
    else:
        target_mph = float(params.get("speed_limit_mph", 25))
        tolerance = float(params.get("tolerance", 0.5))
        low = target_mph - tolerance
        high = target_mph + tolerance
        label_mph = f"~{target_mph:g}"

    with _db_conn() as conn:
        stats_candidates: list[str] = []
        stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_stats_mv"))
        stats_candidates.extend(_cv_relation_candidates(conn, "cv_road_agg"))
        stats_table = _first_existing_relation(conn, list(dict.fromkeys(stats_candidates)))

        if not stats_table:
            return (
                "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"
                "Road speed-limit lookup requires cv_road_stats_mv (not available).\n\n"
                "FINAL DATA RESULTS:\n"
                "No road stats table found."
            )

        stats_cols = _table_column_names(conn, stats_table)
        road_col = _pick_column(conn, stats_table, ["label", "road_name", "road", "name", "ref"])
        road_id_col = _pick_column(conn, stats_table, ["road_segment_id", "way_id", "segment_id"])
        avg_col = _pick_column(conn, stats_table, ["avg_speed_mph", "avg_speed", "speed_avg", "speed"])
        limit_col = _pick_column(
            conn,
            stats_table,
            ["speed_limit_mph", "speed_limit_mode", "avg_speed_limit_mph"],
        )
        points_col = _pick_column(conn, stats_table, ["point_count", "points", "count"])
        filter_col = avg_col if use_avg_speed else limit_col
        metric_label = "average speed" if use_avg_speed else "speed limit"

        if not road_col or not filter_col:
            return (
                "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"
                f"Road stats table is missing label or {metric_label} columns.\n\n"
                "FINAL DATA RESULTS:\n"
                f"Cannot filter roads by {metric_label}."
            )

        where_stats = [
            f"{road_col} IS NOT NULL",
            f"{filter_col} IS NOT NULL",
            f"{filter_col}::float8 BETWEEN %s AND %s",
        ]
        stats_params: list[Any] = [low, high]
        if dataset_id and "dataset_id" in stats_cols:
            where_stats.append("dataset_id = %s")
            stats_params.append(dataset_id)

        road_id_select = f"{road_id_col}::text" if road_id_col else "NULL::text"
        points_select = f"COALESCE({points_col}, 1)" if points_col else "1"
        avg_select = f"{avg_col}::float8 AS avg_speed_mph" if avg_col else "NULL::float8 AS avg_speed_mph"
        limit_select = (
            f"{limit_col}::float8 AS speed_limit_mph" if limit_col else "NULL::float8 AS speed_limit_mph"
        )

        stats_sql = f"""
            SELECT
                {road_col} AS road_name,
                {road_id_select} AS road_segment_id,
                {avg_select},
                {limit_select},
                {points_select}::bigint AS point_count
            FROM {stats_table}
            WHERE {" AND ".join(where_stats)}
            ORDER BY {road_col}, {limit_col}
            LIMIT %s
        """
        stats_params.append(limit_n)
        df = pd.read_sql_query(stats_sql, conn, params=stats_params)

    top_road_names: list[str] = []
    top_segment_ids: list[str] = []
    if not df.empty:
        seen_names: set[str] = set()
        seen_segments: set[str] = set()
        for _, row in df.iterrows():
            name = str(row.get("road_name") or "").strip()
            if name:
                key = name.lower()
                if key not in seen_names:
                    seen_names.add(key)
                    top_road_names.append(name)
            seg = str(row.get("road_segment_id") or "").strip()
            if seg and seg not in seen_segments:
                seen_segments.add(seg)
                top_segment_ids.append(seg)

    if generate_map and (top_road_names or top_segment_ids):
        road_filter_payload: dict[str, Any] = {
            "min_points": 1,
            "limit": min(max(len(top_road_names), len(top_segment_ids)), 40),
        }
        if top_road_names:
            road_filter_payload["road_names"] = top_road_names[:40]
        if top_segment_ids:
            road_filter_payload["road_segment_ids"] = top_segment_ids[:40]
        lines, marker_points = _fetch_segment_geometries_by_ids(top_segment_ids, limit=40)
        if not lines and top_road_names:
            lines, marker_points = _fetch_road_highlight_geometries(top_road_names, limit=40)
        map_metric = "avg speed" if use_avg_speed else "speed limit"
        save_map_for_session(
            {
                "label": f"Roads {label_mph} mph {map_metric}",
                "count": len(marker_points),
                "points": marker_points,
                "lines": lines,
                "overlay": bool(lines or marker_points),
                "analysis_type": "roads_by_speed_limit",
                "roadAggregateFilter": road_filter_payload,
            },
            map_type="traffic",
        )

    if tolerance is None:
        speed_desc = f"between {low:g} and {high:g} mph"
    else:
        speed_desc = f"near {target_mph:g} mph (±{tolerance:g})"
    response_parts = [
        "REPORT FROM TRAFFIC (SQL) SPECIALIST:\n",
        f"Road segments with {metric_label} {speed_desc}.\n",
    ]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append(
            f"No roads found with {metric_label} between {low:g} and {high:g} mph."
        )
    else:
        response_parts.append(
            f"Found {len(df)} segment(s) / road row(s) (showing up to {limit_n}):"
        )
        for _, row in df.head(min(25, len(df))).iterrows():
            road_name = str(row.get("road_name") or "Unknown").strip()
            if use_avg_speed:
                val = row.get("avg_speed_mph")
                val_txt = f"{float(val):.1f}" if pd.notna(val) else "?"
                response_parts.append(f"- {road_name}: avg {val_txt} mph")
            else:
                limit_val = row.get("speed_limit_mph")
                limit_txt = f"{float(limit_val):.0f}" if pd.notna(limit_val) else "?"
                response_parts.append(f"- {road_name}: limit {limit_txt} mph")
        if len(df) > 25:
            response_parts.append(f"... and {len(df) - 25} more.")
        if generate_map:
            response_parts.append(
                f"Map: highlighted {len(top_road_names)} road name(s) on the network layer."
            )
    return "\n".join(response_parts)


def _run_top_hard_braking_roads_from_road_stats(
    params: dict,
    *,
    log,
    dataset: str,
    dataset_id: Optional[str],
    filters: list[dict],
    filter_mode: str,
    apply_drivable_highway_default: bool = True,
) -> Optional[str]:
    """Rank roads by hard_brake_count using cv_road_stats_mv when point events are absent."""
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    group_by = _normalize_top_hard_brake_group_by(params.get("group_by"))
    group_col = {
        "segment": "road_segment_id",
        "road_name": "label",
        "ref": "ref",
    }.get(group_by, "label")
    if group_col == "road_segment_id":
        group_col = "label"

    groupby_req = {
        "group_by": [group_col],
        "aggregations": {
            "hard_brake_count": {
                "fn": "sum",
                "column": "hard_brake_count",
                "alias": "hard_brake_count",
            }
        },
    }
    return _try_run_road_stats_optimized(
        log=log,
        dataset=dataset,
        dataset_id=dataset_id,
        near_crash=None,
        near_workzone=None,
        hard_brake_only=False,
        groupby_req=groupby_req,
        aggregate_req=None,
        sort_cols=["hard_brake_count"],
        sort_dirs=[False],
        head_req=limit_n,
        map_req=None,
        filters=filters,
        filter_mode=filter_mode,
        apply_drivable_highway_default=apply_drivable_highway_default,
    )


def _run_top_hard_braking_roads_operation_impl(
    params: dict,
    *,
    dataset_id: Optional[str],
    col_map: dict[str, str],
    from_sql: str,
    where_sql: str,
    where_params: list[Any],
    lat_expr: str,
    lon_expr: str,
    reasoning: str,
) -> str:
    limit_n = _bounded_int(params.get("limit"), default=5, minimum=1, maximum=25)
    hotspot_limit = _bounded_int(
        params.get("hotspot_limit"),
        default=1200,
        minimum=100,
        maximum=TRAFFIC_MAP_LIMIT_MAX,
    )
    group_by = _normalize_top_hard_brake_group_by(params.get("group_by"))
    group_label = {
        "segment": "road segment",
        "road_name": "road name",
        "ref": "route ref",
    }.get(group_by, "road segment")
    road_ref_expr = col_map.get("road_ref", "NULL::text")

    if group_by == "segment":
        road_stats_group_expr = "road_segment_id"
        road_stats_group_by_clause = "group_key, road_segment_id"
        road_stats_where = "WHERE road_segment_id IS NOT NULL"
        road_name_agg_expr = (
            "CASE "
            "WHEN COALESCE(MAX(road_name), '') = '' THEN 'Segment #' || road_segment_id "
            "ELSE MAX(road_name) || ' (#' || road_segment_id || ')' "
            "END"
        )
        road_segment_agg_expr = "road_segment_id"
        road_ref_agg_expr = "MAX(road_ref)"
    elif group_by == "ref":
        road_stats_group_expr = "LOWER(COALESCE(road_ref, '[unknown ref]'))"
        road_stats_group_by_clause = "group_key"
        road_stats_where = ""
        road_name_agg_expr = (
            "CASE "
            "WHEN COALESCE(MAX(road_ref), '') = '' THEN COALESCE(MAX(road_name), '[unknown road]') "
            "WHEN COALESCE(MAX(road_name), '') = '' THEN COALESCE(MAX(road_ref), '[unknown ref]') "
            "ELSE COALESCE(MAX(road_ref), '[unknown ref]') || ' | ' || COALESCE(MAX(road_name), '[unknown road]') "
            "END"
        )
        road_segment_agg_expr = "MIN(road_segment_id)::text"
        road_ref_agg_expr = "COALESCE(MAX(road_ref), '[unknown ref]')"
    else:
        road_stats_group_expr = "LOWER(COALESCE(road_name, '[unknown road]'))"
        road_stats_group_by_clause = "group_key"
        road_stats_where = ""
        road_name_agg_expr = "COALESCE(MAX(road_name), '[unknown road]')"
        road_segment_agg_expr = "MIN(road_segment_id)::text"
        road_ref_agg_expr = "MAX(road_ref)"

    rank_sql = f"""
        WITH filtered AS (
            SELECT
                NULLIF(TRIM(CAST({col_map["road_name"]} AS text)), '') AS road_name,
                NULLIF(TRIM(CAST({col_map["road_segment_id"]} AS text)), '') AS road_segment_id,
                NULLIF(TRIM(CAST({road_ref_expr} AS text)), '') AS road_ref,
                {col_map["acc_x"]} AS acc_x,
                {col_map["speed"]} AS speed_mph,
                {col_map["speed_over_limit"]} AS speed_over_limit_mph,
                p.ts AS timestamp
            FROM {from_sql}
            WHERE {where_sql}
        ),
        road_stats AS (
            SELECT
                {road_stats_group_expr} AS group_key,
                {road_segment_agg_expr} AS road_segment_id,
                {road_ref_agg_expr} AS road_ref,
                {road_name_agg_expr} AS road_name,
                COUNT(*)::bigint AS hard_brake_count,
                AVG(acc_x)::float8 AS avg_acc_x,
                MIN(acc_x)::float8 AS min_acc_x,
                AVG(speed_mph)::float8 AS avg_speed_mph,
                AVG(speed_over_limit_mph)::float8 AS avg_speed_over_limit_mph,
                MIN(timestamp) AS start_ts,
                MAX(timestamp) AS end_ts
            FROM filtered
            {road_stats_where}
            GROUP BY {road_stats_group_by_clause}
        )
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY hard_brake_count DESC, ABS(COALESCE(avg_acc_x, 0.0)) DESC, road_name ASC
            ) AS rank,
            road_name,
            road_segment_id,
            road_ref,
            hard_brake_count,
            avg_acc_x,
            min_acc_x,
            avg_speed_mph,
            avg_speed_over_limit_mph,
            start_ts,
            end_ts
        FROM road_stats
        ORDER BY rank
        LIMIT %s
    """
    with _db_conn() as conn:
        df = pd.read_sql_query(rank_sql, conn, params=list(where_params) + [limit_n])
    df = _apply_codebook_labels(df, dataset_id=dataset_id)

    top_road_names: list[str] = []
    top_segment_ids: list[str] = []
    top_refs: list[str] = []
    if not df.empty:
        if "road_name" in df.columns:
            seen_names: set[str] = set()
            for raw in df["road_name"].tolist():
                if raw is None:
                    continue
                name = str(raw).strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                top_road_names.append(name)
        if "road_segment_id" in df.columns:
            seen_segments: set[str] = set()
            for raw in df["road_segment_id"].tolist():
                seg = str(raw or "").strip()
                if not seg:
                    continue
                if seg in seen_segments:
                    continue
                seen_segments.add(seg)
                top_segment_ids.append(seg)
        if "road_ref" in df.columns:
            seen_refs: set[str] = set()
            for raw in df["road_ref"].tolist():
                ref = str(raw or "").strip()
                if not ref:
                    continue
                key = ref.lower()
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                top_refs.append(ref)

    hotspot_rows: list[dict[str, Any]] = []
    scope_count = 0
    if top_road_names or top_segment_ids or top_refs:
        scope_clauses: list[str] = []
        scope_params: list[Any] = []
        if group_by == "segment" and top_segment_ids:
            scope_clauses.append(
                f"NULLIF(TRIM(CAST({col_map['road_segment_id']} AS text)), '') = ANY(%s::text[])"
            )
            scope_params.append(top_segment_ids)
            scope_count = len(top_segment_ids)
        elif group_by == "ref" and top_refs:
            scope_clauses.append(
                f"LOWER(COALESCE(NULLIF(TRIM(CAST({road_ref_expr} AS text)), ''), '[unknown ref]')) = ANY(%s::text[])"
            )
            scope_params.append([ref.lower() for ref in top_refs])
            scope_count = len(top_refs)
        elif top_road_names:
            scope_clauses.append(f"LOWER({col_map['road_name']}) = ANY(%s::text[])")
            scope_params.append([name.lower() for name in top_road_names])
            scope_count = len(top_road_names)

        hotspot_sql = f"""
            SELECT
                {lat_expr} AS latitude,
                {lon_expr} AS longitude,
                p.ts AS timestamp,
                {col_map["road_name"]} AS road_name,
                {col_map["road_segment_id"]} AS road_segment_id,
                {road_ref_expr} AS road_ref,
                {col_map["speed"]} AS speed,
                {col_map["SpeedLimitMPH"]} AS "speedLimit",
                {col_map["speed_over_limit"]} AS speed_over_limit,
                {col_map["acc_x"]} AS acc_x,
                {col_map["acc_y"]} AS acc_y,
                'HardBrake'::text AS type,
                'HardBrake'::text AS point_type
            FROM {from_sql}
            WHERE {where_sql}
              AND {lat_expr} IS NOT NULL
              AND {lon_expr} IS NOT NULL
              AND ({' OR '.join(scope_clauses)})
            ORDER BY ABS({col_map["acc_x"]}) DESC NULLS LAST, p.ts DESC
            LIMIT %s
        """
        hotspot_params = list(where_params) + scope_params + [hotspot_limit]
        with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(hotspot_sql, hotspot_params)
            hotspot_rows = cur.fetchall()

        map_payload = _make_map_payload(
            hotspot_rows,
            label=f"Top {scope_count or len(top_road_names)} groups by hard-braking events ({group_label})",
            hard_brake_only=True,
        )
        road_scope_filter: dict[str, Any] = {
            "min_points": 1,
            "limit": min(max(scope_count, len(top_road_names), len(top_segment_ids), len(top_refs)), 40),
            "group_by": group_by,
        }
        if top_road_names and group_by == "road_name":
            road_scope_filter["road_names"] = top_road_names[:40]
        if top_segment_ids:
            road_scope_filter["road_segment_ids"] = top_segment_ids[:40]
            road_scope_filter["road_segment_id"] = top_segment_ids[0]
        if top_refs:
            road_scope_filter["road_refs"] = top_refs[:40]
        map_payload["roadAggregateFilter"] = road_scope_filter
        map_payload["analysis_type"] = "top_hard_braking_roads"
        map_payload["overlay"] = bool(hotspot_rows)
        save_map_for_session(map_payload, map_type="traffic")

    response_parts = ["REPORT FROM TRAFFIC (SQL) SPECIALIST:\n"]
    if reasoning:
        response_parts.append(f"Analysis Plan: {reasoning}\n")
    response_parts.append(f"CV dataset scope: {dataset_id or '__all__'}")
    response_parts.append("Ranking metric: hard-braking event count, descending.")
    response_parts.append(f"Grouping: {group_label}.")
    if scope_count > 0:
        response_parts.append(
            f"Map output: highlighted {scope_count} ranked groups and {len(hotspot_rows)} hard-braking points."
        )

    response_parts.append("\nFINAL DATA RESULTS:")
    if df.empty:
        response_parts.append("Result is an empty table (no hard-braking events matched the filters).")
    else:
        response_parts.append(
            f"Top {min(limit_n, len(df))} groups by hard-braking events (grouped by {group_label}):"
        )
        for _, row in df.head(limit_n).iterrows():
            rank = int(row.get("rank")) if pd.notna(row.get("rank")) else None
            road_name = str(row.get("road_name") or "Unknown road").strip()
            event_count = int(row.get("hard_brake_count")) if pd.notna(row.get("hard_brake_count")) else 0
            avg_acc_x = float(row.get("avg_acc_x")) if pd.notna(row.get("avg_acc_x")) else 0.0
            if rank is None:
                response_parts.append(
                    f"- {road_name}: {event_count:,} events (avg acc_x {avg_acc_x:.3f} g)"
                )
            else:
                response_parts.append(
                    f"{rank}. {road_name}: {event_count:,} events (avg acc_x {avg_acc_x:.3f} g)"
                )
    return "\n".join(response_parts)

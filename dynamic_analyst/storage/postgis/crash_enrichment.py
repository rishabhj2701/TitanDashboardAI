"""RAMS/CV enrichment schema and materialized views for crash events."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from dynamic_analyst.data_paths import PATH_RAMS_RAW

from .table_names import APP_EVENTS

logger = logging.getLogger("adk_server")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_INIT = _REPO_ROOT / "db" / "init"


def _apply_sql_file(cur, path: Path) -> None:
    if path.is_file():
        cur.execute(path.read_text(encoding="utf-8"))


def ensure_crash_enrichment_schema(cur) -> None:
    """Apply Iowa route helpers and cross-dataset enrichment SQL."""
    cur.execute("SELECT to_regprocedure('public.iowa_route_base_ref(text)') IS NOT NULL")
    if not cur.fetchone()[0]:
        _apply_sql_file(cur, _DB_INIT / "007_iowa_route_helpers.sql")

    cur.execute("SELECT to_regclass('app_data.crash_road_enriched')")
    has_enriched = cur.fetchone()[0] is not None
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'app_data' AND table_name = 'crash_road_enriched'
          AND column_name = 'cv_near_speed_mph'
        """
    )
    needs_v2 = not has_enriched or int(cur.fetchone()[0] or 0) == 0

    if needs_v2:
        _apply_sql_file(cur, _DB_INIT / "010_cross_dataset_enrichment.sql")
        logger.info("crash_enrichment.applied_sql path=%s", "010_cross_dataset_enrichment.sql")
    elif not has_enriched:
        _apply_sql_file(cur, _DB_INIT / "008_crash_road_enriched.sql")


def backfill_rams_county_columns(cur) -> int:
    """Load COUNTY_NUMBER / ROUTEID / ROAD_SYSTEM from RAMS NDJSON into rams_roads_match."""
    cur.execute(
        """
        SELECT COUNT(*) FROM public.rams_roads_match
        WHERE county_number IS NULL
        """
    )
    missing = int(cur.fetchone()[0])
    if missing == 0:
        return 0

    files = [
        PATH_RAMS_RAW / "ramps_S_tagged.ndjson",
        PATH_RAMS_RAW / "ramps_C_tagged.ndjson",
    ]
    batch: list[tuple] = []
    updated = 0

    def flush() -> None:
        nonlocal updated
        if not batch:
            return
        cur.executemany(
            """
            UPDATE public.rams_roads_match
            SET
              county_number = COALESCE(%s, county_number),
              routeid_raw = COALESCE(%s, routeid_raw),
              road_system = COALESCE(%s, road_system)
            WHERE way_id = %s
            """,
            batch,
        )
        updated += len(batch)
        batch.clear()

    for path in files:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    feat = json.loads(line)
                except json.JSONDecodeError:
                    continue
                props = feat.get("properties") or {}
                oid = props.get("OBJECTID")
                if oid is None:
                    continue
                way_id = int(oid)
                county = props.get("COUNTY_NUMBER")
                try:
                    county_number = int(county) if county is not None else None
                except (TypeError, ValueError):
                    county_number = None
                routeid_raw = props.get("ROUTEID") or props.get("route_id")
                road_system = props.get("ROAD_SYSTEM")
                batch.append(
                    (
                        county_number,
                        str(routeid_raw) if routeid_raw else None,
                        str(road_system) if road_system is not None else None,
                        way_id,
                    )
                )
                if len(batch) >= 5000:
                    flush()
    flush()
    return updated


def refresh_crash_road_enriched(cur) -> Dict[str, int]:
    """Refresh CV indexes and crash enrichment MVs."""
    cur.execute("SELECT to_regclass('public.cv_segment_measure_index')")
    if cur.fetchone()[0] is None:
        ensure_crash_enrichment_schema(cur)

    cur.execute("REFRESH MATERIALIZED VIEW public.cv_segment_measure_index")
    cur.execute("REFRESH MATERIALIZED VIEW public.cv_route_level_stats")
    cur.execute("REFRESH MATERIALIZED VIEW app_data.crash_road_enriched")

    return {
        "cv_segment_measure_index": cur.rowcount,
        "cv_route_level_stats": cur.rowcount,
        "crash_road_enriched": cur.rowcount,
    }


def sync_cross_links_to_events(cur, dataset_id: str) -> int:
    """Copy enrichment fields into events.props.cross_links for API/agents."""
    cur.execute(
        f"""
        UPDATE {APP_EVENTS} AS e
        SET props = e.props || jsonb_build_object(
          'cross_links',
          jsonb_strip_nulls(jsonb_build_object(
            'routeid_raw', c.routeid_raw,
            'route_base_ref', c.route_base_ref,
            'route_prefix', c.route_prefix,
            'crash_measure', c.crash_measure,
            'crash_county', c.crash_county,
            'rams_ref', c.rams_ref,
            'rams_county_number', c.rams_county_number,
            'county_match', c.county_match,
            'road_name', c.road_name,
            'speed_limit_mph', c.speed_limit_mph,
            'avg_speed_mph', c.avg_speed_mph,
            'cv_link_method', c.cv_link_method,
            'rams_match_method', c.rams_match_method,
            'has_cv_coverage', c.has_cv_coverage,
            'cv_near_speed_mph', c.cv_near_speed_mph,
            'cv_near_journey_count', c.cv_near_journey_count,
            'cv_near_hard_brake_count', c.cv_near_hard_brake_count,
            'cv_segment_measure', c.cv_segment_measure,
            'segment_avg_speed_mph', c.segment_avg_speed_mph,
            'route_avg_speed_mph', c.route_avg_speed_mph
          ))
        )
        FROM app_data.crash_road_enriched c
        WHERE c.id = e.id AND e.dataset_id = %s
        """,
        (dataset_id,),
    )
    return int(cur.rowcount)


def crash_enrichment_stats(cur, dataset_id: Optional[str] = None) -> Dict[str, Any]:
    """Summary counts for crash ↔ RAMS/CV linkage."""
    cur.execute("SELECT to_regclass('app_data.crash_road_enriched')")
    if cur.fetchone()[0] is None:
        return {"ready": False}

    ds_filter = ""
    params: tuple = ()
    if dataset_id:
        ds_filter = "WHERE dataset_id = %s"
        params = (dataset_id,)

    cur.execute(
        f"""
        SELECT
          COUNT(*)::bigint AS total,
          COUNT(*) FILTER (WHERE way_id IS NOT NULL)::bigint AS with_way_id,
          COUNT(*) FILTER (WHERE cv_link_method = 'segment_way_id')::bigint AS cv_segment,
          COUNT(*) FILTER (WHERE cv_link_method = 'route_measure_cv')::bigint AS cv_measure,
          COUNT(*) FILTER (WHERE cv_link_method = 'route_ref')::bigint AS cv_route,
          COUNT(*) FILTER (WHERE cv_link_method = 'rams_only')::bigint AS rams_only,
          COUNT(*) FILTER (WHERE county_match IS TRUE)::bigint AS county_match,
          COUNT(*) FILTER (WHERE road_name IS NOT NULL AND BTRIM(road_name) <> '')::bigint AS named_roads,
          COUNT(*) FILTER (WHERE avg_speed_mph IS NOT NULL)::bigint AS with_cv_speed,
          COUNT(*) FILTER (WHERE has_cv_coverage IS TRUE)::bigint AS has_cv_coverage
        FROM app_data.crash_road_enriched
        {ds_filter}
        """,
        params,
    )
    row = cur.fetchone()
    keys = (
        "total",
        "with_way_id",
        "cv_segment",
        "cv_measure",
        "cv_route",
        "rams_only",
        "county_match",
        "named_roads",
        "with_cv_speed",
        "has_cv_coverage",
    )
    stats = dict(zip(keys, row))
    stats["ready"] = True

    if dataset_id:
        cur.execute(
            f"""
            SELECT COUNT(*)::bigint AS total,
                   COUNT(*) FILTER (WHERE way_id IS NOT NULL)::bigint AS matched,
                   COUNT(*) FILTER (WHERE props ? 'cross_links')::bigint AS with_cross_links
            FROM {APP_EVENTS}
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        )
        ev_total, ev_matched, with_cross = cur.fetchone()
        stats["events_total"] = int(ev_total)
        stats["events_matched"] = int(ev_matched)
        stats["events_cross_links"] = int(with_cross)

        cur.execute(
            f"""
            SELECT rams_match_method, COUNT(*)::bigint
            FROM {APP_EVENTS}
            WHERE dataset_id = %s AND props->>'match_method' IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
            """,
            (dataset_id,),
        )
        stats["rams_match_methods"] = {r[0]: int(r[1]) for r in cur.fetchall()}

    return stats

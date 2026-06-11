from __future__ import annotations

import logging
import re
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException

from dynamic_analyst import postgis_store
from dynamic_analyst.session_state import get_active_user, set_active_session
from dynamic_analyst.storage.postgis.table_names import APP_USER_CV_RUN_CONFIG

router = APIRouter()
logger = logging.getLogger("adk_server")
_CV_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id header")
    set_active_session(x_session_id)
    return x_session_id


def _require_valid_cv_schema_name(schema_name: str) -> str:
    schema = (schema_name or "").strip()
    if not schema or not _CV_SCHEMA_NAME_RE.match(schema):
        raise ValueError(f"Invalid schema name '{schema_name}'.")
    return schema


def _active_cv_schema_name(cur) -> Optional[str]:
    try:
        uid = (get_active_user() or "dev-user").strip() or "dev-user"
        cur.execute("SELECT to_regclass('public.cv_runs') AS rel_runs")
        row = cur.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            if not row.get("rel_runs"):
                return None
        else:
            if not row[0]:
                return None
        row = None
        cur.execute("SELECT to_regclass(%s) AS rel_cfg", (APP_USER_CV_RUN_CONFIG,))
        user_cfg = cur.fetchone()
        has_user_cfg = (user_cfg or {}).get("rel_cfg") if isinstance(user_cfg, dict) else bool(user_cfg and user_cfg[0])
        if has_user_cfg and uid:
            cur.execute(
                f"""
                SELECT r.schema_name
                FROM {APP_USER_CV_RUN_CONFIG} c
                JOIN public.cv_runs r ON r.run_id = c.active_run_id
                WHERE c.user_id = %s
                LIMIT 1
                """,
                (uid,),
            )
            row = cur.fetchone()
        if not row:
            cur.execute("SELECT to_regclass('public.cv_run_config') AS rel_cfg")
            global_cfg = cur.fetchone()
            has_global_cfg = (global_cfg or {}).get("rel_cfg") if isinstance(global_cfg, dict) else bool(global_cfg and global_cfg[0])
            if has_global_cfg:
                cur.execute(
                    """
                    SELECT r.schema_name
                    FROM public.cv_run_config c
                    JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.id = 1
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        if not row:
            return None
        schema_name = row.get("schema_name") if isinstance(row, dict) else row[0]
        if not schema_name:
            return None
        return _require_valid_cv_schema_name(str(schema_name))
    except Exception:
        return None


def _cv_relation_candidates(cur, relation: str) -> list[str]:
    candidates: list[str] = []
    schema_name = _active_cv_schema_name(cur)
    if schema_name:
        candidates.append(f"{schema_name}.{relation}")
    candidates.extend([relation, f"public.{relation}"])
    deduped: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        if name and name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _relation_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (name,))
    row = cur.fetchone()
    if not row:
        return False
    if isinstance(row, dict):
        return bool(row.get("to_regclass"))
    return bool(row[0])


def _first_existing_relation(cur, candidates: list[str]) -> Optional[str]:
    for name in candidates:
        if _relation_exists(cur, name):
            return name
    return None


def _resolve_cv_points_relation(cur) -> Optional[str]:
    return _first_existing_relation(cur, _cv_relation_candidates(cur, "cv_points"))


def _attrs_road_name_expr(attrs_expr: str = "attrs") -> str:
    return (
        f"COALESCE("
        f"NULLIF({attrs_expr}->>'road',''), "
        f"NULLIF({attrs_expr}->>'RoadName',''), "
        f"NULLIF({attrs_expr}->>'roadName',''), "
        f"NULLIF({attrs_expr}->>'road_name','')"
        f")"
    )


@router.get("/api/cv/roads")
def get_available_roads(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    _require_session(x_session_id)

    try:
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cv_table = _resolve_cv_points_relation(cur)
            if not cv_table:
                raise ValueError("cv_points table not found.")
            road_expr = _attrs_road_name_expr("attrs")
            query = f"""
                SELECT DISTINCT {road_expr} AS road_name
                FROM {cv_table}
                WHERE {road_expr} IS NOT NULL
                ORDER BY road_name
                LIMIT 500
            """
            cur.execute(query)
            rows = cur.fetchall()

        roads: list[str] = []
        for row in rows:
            value = row.get("road_name") if isinstance(row, dict) else row[0]
            if value:
                roads.append(str(value))

        return {
            "status": "success",
            "count": len(roads),
            "roads": roads,
        }
    except Exception as exc:
        logger.error("Failed to fetch roads: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

"""CV dataset helper services."""

from __future__ import annotations

import re
from typing import Optional

from .. import postgis_store
from ..session_state import get_active_session, get_active_user
from ..storage.postgis.table_names import APP_DATASETS, APP_USER_CV_RUN_CONFIG

_CV_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _first_existing_relation(conn, candidates: list[str]) -> Optional[str]:
    for relation in candidates:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (relation,))
            if cur.fetchone()[0]:
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


def _active_cv_schema_name(conn) -> Optional[str]:
    try:
        with conn.cursor() as cur:
            user_id = (get_active_user() or "").strip()
            cur.execute("SELECT to_regclass(%s)", (APP_USER_CV_RUN_CONFIG,))
            user_cfg_rel = cur.fetchone()
            if user_cfg_rel and user_cfg_rel[0] and user_id:
                cur.execute(
                    f"""
                    SELECT r.schema_name
                    FROM {APP_USER_CV_RUN_CONFIG} c
                    JOIN public.cv_runs r ON r.run_id = c.active_run_id
                    WHERE c.user_id = %s
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                schema_name = str(row[0]).strip() if row and row[0] else ""
                if schema_name and _CV_SCHEMA_NAME_RE.match(schema_name):
                    return schema_name

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
            schema_name = str(row[0]).strip() if row and row[0] else ""
            return schema_name or None
    except Exception:
        return None


def get_active_cv_run_id(user_id: Optional[str] = None) -> Optional[str]:
    uid = (user_id or get_active_user() or "").strip()
    try:
        with postgis_store._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (APP_USER_CV_RUN_CONFIG,))
            user_cfg_rel = cur.fetchone()
            if user_cfg_rel and user_cfg_rel[0] and uid:
                cur.execute(
                    f"SELECT active_run_id FROM {APP_USER_CV_RUN_CONFIG} WHERE user_id = %s LIMIT 1",
                    (uid,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0]).strip()

            cur.execute("SELECT to_regclass('public.cv_run_config')")
            cfg_rel = cur.fetchone()
            if cfg_rel and cfg_rel[0]:
                cur.execute("SELECT active_run_id FROM public.cv_run_config WHERE id = 1")
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0]).strip()
    except Exception:
        return None
    return None


def _cv_relation_candidates(conn, relation_name: str) -> list[str]:
    candidates: list[str] = []
    active_schema = _active_cv_schema_name(conn)
    if active_schema:
        candidates.append(f"{active_schema}.{relation_name}")
    candidates.append(f"public.{relation_name}")
    candidates.append(relation_name)
    return list(dict.fromkeys(candidates))


def latest_cv_dataset_id() -> Optional[str]:
    """Return the latest CV dataset id visible to the active session."""
    uid = (get_active_user() or "").strip()
    if uid:
        try:
            with postgis_store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE owner_user_id=%s AND status='ready' AND entity_type='cv'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uid,),
                )
                row = cur.fetchone()
                if row:
                    return row[0]
        except Exception:
            # Fall through to cv_points lookup when datasets metadata is unavailable.
            pass

    # If user has an active CV run selection, use it as the CV dataset scope.
    active_run_id = get_active_cv_run_id(uid)
    if active_run_id:
        return active_run_id

    sid = get_active_session()
    if sid:
        try:
            with postgis_store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT dataset_id
                    FROM {APP_DATASETS}
                    WHERE session_id=%s AND status='ready' AND entity_type='cv'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (sid,),
                )
                row = cur.fetchone()
                if row:
                    return row[0]
        except Exception:
            pass

    try:
        with postgis_store._conn() as conn:
            cv_points_table = _first_existing_relation(conn, _cv_relation_candidates(conn, "cv_points")) or "cv_points"
            columns = _table_column_names(conn, cv_points_table)
            if "dataset_id" not in columns:
                return None
            with conn.cursor() as cur:
                cur.execute(f"SELECT dataset_id FROM {cv_points_table} ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from dynamic_analyst import postgis_store
from dynamic_analyst.session_state import get_active_user, set_active_session
from dynamic_analyst.storage.postgis.table_names import APP_USER_CV_RUN_CONFIG

router = APIRouter()
logger = logging.getLogger("adk_server")

_CV_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_clear_cv_cache_callback: Callable[[], None] = lambda: None


def set_cv_cache_clearer(callback: Optional[Callable[[], None]]) -> None:
    global _clear_cv_cache_callback
    _clear_cv_cache_callback = callback or (lambda: None)


class CVRunSwitchRequest(BaseModel):
    run_id: str


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


def _active_user_id() -> str:
    return (get_active_user() or "dev-user").strip() or "dev-user"


def _ensure_user_cv_run_config(cur) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {APP_USER_CV_RUN_CONFIG} (
            user_id TEXT PRIMARY KEY,
            active_run_id TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS user_cv_run_config_active_idx ON {APP_USER_CV_RUN_CONFIG} (active_run_id)"
    )


def _resolve_active_run_id(cur, user_id: str) -> Optional[str]:
    if user_id:
        cur.execute("SELECT to_regclass(%s) AS rel", (APP_USER_CV_RUN_CONFIG,))
        if (cur.fetchone() or {}).get("rel"):
            cur.execute(
                f"SELECT active_run_id FROM {APP_USER_CV_RUN_CONFIG} WHERE user_id = %s LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone() or {}
            run_id = str(row.get("active_run_id") or "").strip()
            if run_id:
                return run_id

    cur.execute("SELECT to_regclass('public.cv_run_config') AS rel")
    if (cur.fetchone() or {}).get("rel"):
        cur.execute("SELECT active_run_id FROM public.cv_run_config WHERE id = 1")
        row = cur.fetchone() or {}
        run_id = str(row.get("active_run_id") or "").strip()
        if run_id:
            return run_id
    return None


@router.get("/api/cv/runs")
def list_cv_runs_endpoint(
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    _current_user: dict = Depends(get_current_user),
):
    _require_session(x_session_id)
    try:
        user_id = _active_user_id()
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            active_run_id = _resolve_active_run_id(cur, user_id)

            cur.execute("SELECT to_regclass('public.cv_runs') AS rel")
            if not (cur.fetchone() or {}).get("rel"):
                return {"status": "success", "active_run_id": active_run_id, "runs": []}
            cur.execute(
                """
                SELECT
                    run_id,
                    schema_name,
                    created_at,
                    display_name,
                    season_tag,
                    state_code,
                    point_count,
                    ts_start,
                    ts_end,
                    is_visible
                FROM public.cv_runs
                ORDER BY created_at DESC, run_id DESC
                """
            )
            runs = []
            for row in cur.fetchall():
                run = dict(row)
                state_code = (run.get("state_code") or "").strip()
                run["season"] = run.get("season_tag")
                run["region"] = state_code
                run["states"] = [state_code] if state_code else []
                run["is_selectable"] = run.get("is_visible")
                run["is_active"] = bool(active_run_id and run.get("run_id") == active_run_id)
                runs.append(run)
        logger.info(
            json.dumps(
                {
                    "event": "audit_cv_runs_list",
                    "user_id": user_id,
                    "active_run_id": active_run_id,
                    "count": len(runs),
                }
            )
        )

        return {"status": "success", "active_run_id": active_run_id, "runs": runs}
    except Exception as exc:
        logger.error("list cv runs failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/cv/active-run")
def get_active_cv_run_endpoint(
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    _current_user: dict = Depends(get_current_user),
):
    _require_session(x_session_id)
    try:
        user_id = _active_user_id()
        with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            active_run_id = _resolve_active_run_id(cur, user_id)
            if not active_run_id:
                return {"status": "success", "active_run": None}

            cur.execute("SELECT to_regclass('public.cv_runs') AS rel")
            if not (cur.fetchone() or {}).get("rel"):
                return {
                    "status": "success",
                    "active_run": {
                        "run_id": active_run_id,
                        "schema_name": None,
                        "created_at": None,
                    },
                }

            cur.execute(
                """
                SELECT run_id, schema_name, created_at
                FROM public.cv_runs
                WHERE run_id = %s
                LIMIT 1
                """
                ,
                (active_run_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "status": "success",
                    "active_run": {
                        "run_id": active_run_id,
                        "schema_name": None,
                        "created_at": None,
                    },
                }
            return {
                "status": "success",
                "active_run": {
                    "run_id": row.get("run_id"),
                    "schema_name": row.get("schema_name"),
                    "created_at": row.get("created_at"),
                },
            }
    except Exception as exc:
        logger.error("get active cv run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/cv/active-run")
def set_active_cv_run_endpoint(
    payload: CVRunSwitchRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    _current_user: dict = Depends(get_current_user),
):
    _require_session(x_session_id)
    run_id = (payload.run_id or "").strip()
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required.")

    try:
        run_row: Optional[dict[str, Any]] = None
        schema_name = ""
        user_id = _active_user_id()
        warnings: list[str] = []

        try:
            with postgis_store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL lock_timeout = '1500ms'")
                cur.execute("SET LOCAL statement_timeout = '8000ms'")
                _ensure_user_cv_run_config(cur)

                cur.execute(
                    """
                    SELECT run_id, schema_name, created_at
                    FROM public.cv_runs
                    WHERE run_id = %s
                    LIMIT 1
                    """,
                    (run_id,),
                )
                run_row = cur.fetchone()
                if not run_row:
                    raise HTTPException(status_code=404, detail=f"CV run '{run_id}' was not found.")

                schema_name = _require_valid_cv_schema_name(str(run_row.get("schema_name") or ""))

                cur.execute(
                    f"""
                    UPDATE {APP_USER_CV_RUN_CONFIG}
                    SET active_run_id = %s
                    WHERE user_id = %s
                    """,
                    (run_id, user_id),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        f"""
                        INSERT INTO {APP_USER_CV_RUN_CONFIG}(user_id, active_run_id)
                        VALUES (%s, %s)
                        """,
                        (user_id, run_id),
                    )
        except psycopg2.Error as db_lock_error:
            if getattr(db_lock_error, "pgcode", None) in {"55P03", "57014"}:
                raise HTTPException(
                    status_code=409,
                    detail="CV run switch is busy (database lock timeout). Please retry in a few seconds.",
                )
            raise

        views_repointed = False
        try:
            _clear_cv_cache_callback()
        except Exception as clear_error:
            logger.warning("set active cv run: failed to clear CV map caches: %s", clear_error)
            warnings.append("CV caches were not fully cleared after run switch; retry if stale data appears.")
        logger.info(
            json.dumps(
                {
                    "event": "audit_cv_active_run_set",
                    "user_id": user_id,
                    "run_id": run_id,
                    "schema_name": schema_name,
                    "status": "success",
                }
            )
        )

        return {
            "status": "success",
            "active_run": {
                "run_id": run_row.get("run_id") if isinstance(run_row, dict) else run_id,
                "schema_name": schema_name,
                "created_at": run_row.get("created_at") if isinstance(run_row, dict) else None,
            },
            "execution_mode": "schema_qualified",
            "warnings": warnings,
            "views_repointed": views_repointed,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("set active cv run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

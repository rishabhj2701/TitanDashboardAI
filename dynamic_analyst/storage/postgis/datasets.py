from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

from .table_names import APP_DATASET_CODEBOOK, APP_DATASETS, APP_EVENTS


def _owner_clause(sid, uid):
    """Return (where_fragment, param) for user_id or session_id scoping."""
    if uid:
        return "user_id = %s", uid
    return "session_id = %s", sid


def create_dataset(
    name: str,
    entity_type: str,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    ensure_core_tables_fn,
) -> Dict[str, Any]:
    dataset_id = f"{entity_type}_{uuid.uuid4().hex[:12]}"
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    with conn_factory() as conn, conn.cursor() as cur:
        ensure_core_tables_fn(cur)
        cur.execute(
            """
            INSERT INTO """ + APP_DATASETS + """(dataset_id, owner_user_id, session_id, user_id, name, entity_type, status, mapping, provenance, stats)
            VALUES (%s,%s,%s,%s,%s,%s,'processing','{}'::jsonb,'[]'::jsonb,'{}'::jsonb)
            """,
            (dataset_id, uid, sid, uid, name, entity_type),
        )
    return {
        "dataset_id": dataset_id,
        "owner_user_id": uid,
        "session_id": sid,
        "name": name,
        "entity_type": entity_type,
    }


def mark_ready(
    dataset_id: str,
    stats: Dict[str, Any],
    provenance_step: Dict[str, Any] | None,
    *,
    conn_factory,
    sid_fn,
    uid_fn=None,
    ensure_core_tables_fn,
) -> None:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    with conn_factory() as conn, conn.cursor() as cur:
        ensure_core_tables_fn(cur)
        if provenance_step:
            cur.execute(
                f"""
                UPDATE {APP_DATASETS}
                SET status='ready',
                    stats = COALESCE(stats,'{{}}'::jsonb) || %s::jsonb,
                    provenance = COALESCE(provenance,'[]'::jsonb) || %s::jsonb
                WHERE dataset_id=%s AND {owner_where}
                """,
                (json.dumps(stats), json.dumps([provenance_step]), dataset_id, owner_val),
            )
        else:
            cur.execute(
                f"""
                UPDATE {APP_DATASETS}
                SET status='ready',
                    stats = COALESCE(stats,'{{}}'::jsonb) || %s::jsonb
                WHERE dataset_id=%s AND {owner_where}
                """,
                (json.dumps(stats), dataset_id, owner_val),
            )


def list_datasets(*, conn_factory, sid_fn, uid_fn=None) -> List[Dict[str, Any]]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    try:
        with conn_factory() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT dataset_id, name, entity_type, status, created_at, stats
                FROM {APP_DATASETS}
                WHERE {owner_where}
                ORDER BY created_at DESC
                """,
                (owner_val,),
            )
            return [dict(r) for r in cur.fetchall()]
    except psycopg2.errors.UndefinedTable:
        return []


def clear_session_data(session_id: str, owner_user_id: str, *, conn_factory) -> Dict[str, Any]:
    if not session_id:
        raise ValueError("session_id is required")
    if not owner_user_id:
        raise ValueError("owner_user_id is required")
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {APP_EVENTS} WHERE session_id=%s AND owner_user_id=%s",
            (session_id, owner_user_id),
        )
        events_count = int(cur.fetchone()[0])
        cur.execute(
            f"SELECT COUNT(*) FROM {APP_DATASETS} WHERE session_id=%s AND owner_user_id=%s",
            (session_id, owner_user_id),
        )
        datasets_count = int(cur.fetchone()[0])
        cur.execute(
            f"DELETE FROM {APP_EVENTS} WHERE session_id=%s AND owner_user_id=%s",
            (session_id, owner_user_id),
        )
        cur.execute(
            f"DELETE FROM {APP_DATASETS} WHERE session_id=%s AND owner_user_id=%s",
            (session_id, owner_user_id),
        )
    return {"events_deleted": events_count, "datasets_deleted": datasets_count}


def delete_dataset(dataset_id: str, owner_user_id: str, *, conn_factory) -> Dict[str, Any]:
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if not owner_user_id:
        raise ValueError("owner_user_id is required")
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT session_id FROM {APP_DATASETS} WHERE dataset_id=%s AND owner_user_id=%s LIMIT 1",
            (dataset_id, owner_user_id),
        )
        row = cur.fetchone()
        if not row:
            return {"dataset_id": dataset_id, "events_deleted": 0, "datasets_deleted": 0, "codebook_deleted": 0}
        dataset_session_id = str(row[0])
        datasets_count = 1

        cur.execute(
            f"SELECT COUNT(*) FROM {APP_EVENTS} WHERE dataset_id=%s AND owner_user_id=%s",
            (dataset_id, owner_user_id),
        )
        events_count = int(cur.fetchone()[0])

        cur.execute(
            f"DELETE FROM {APP_EVENTS} WHERE dataset_id=%s AND owner_user_id=%s",
            (dataset_id, owner_user_id),
        )
        cur.execute(
            f"DELETE FROM {APP_DATASETS} WHERE dataset_id=%s AND owner_user_id=%s",
            (dataset_id, owner_user_id),
        )

        codebook_deleted = 0
        cur.execute(
            f"SELECT COUNT(*) FROM {APP_DATASETS} WHERE session_id=%s AND owner_user_id=%s",
            (dataset_session_id, owner_user_id),
        )
        remaining_in_session = int(cur.fetchone()[0])
        if remaining_in_session <= 0:
            cur.execute("SELECT to_regclass(%s)", (APP_DATASET_CODEBOOK,))
            codebook_table_exists = cur.fetchone()[0] is not None
            if codebook_table_exists:
                cur.execute(
                    f"SELECT COUNT(*) FROM {APP_DATASET_CODEBOOK} WHERE session_id=%s AND owner_user_id=%s",
                    (dataset_session_id, owner_user_id),
                )
                codebook_deleted = int(cur.fetchone()[0])
                if codebook_deleted > 0:
                    cur.execute(
                        f"DELETE FROM {APP_DATASET_CODEBOOK} WHERE session_id=%s AND owner_user_id=%s",
                        (dataset_session_id, owner_user_id),
                    )

    return {
        "dataset_id": dataset_id,
        "events_deleted": events_count,
        "datasets_deleted": datasets_count,
        "codebook_deleted": codebook_deleted,
    }


def clear_all_data(*, conn_factory) -> Dict[str, Any]:
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {APP_EVENTS}")
        events_count = int(cur.fetchone()[0])
        cur.execute(f"SELECT COUNT(*) FROM {APP_DATASETS}")
        datasets_count = int(cur.fetchone()[0])
        cur.execute(f"DELETE FROM {APP_EVENTS}")
        cur.execute(f"DELETE FROM {APP_DATASETS}")
    return {"events_deleted": events_count, "datasets_deleted": datasets_count}


def get_dataset(dataset_id: str, *, conn_factory, sid_fn, uid_fn=None) -> Dict[str, Any]:
    sid = sid_fn()
    uid = uid_fn() if uid_fn else None
    owner_where, owner_val = _owner_clause(sid, uid)
    with conn_factory() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {APP_DATASETS} WHERE dataset_id=%s AND {owner_where}", (dataset_id, owner_val))
        row = cur.fetchone()
        if not row:
            raise ValueError("Dataset not found for this user.")
        return dict(row)

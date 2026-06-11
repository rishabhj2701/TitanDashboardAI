from typing import Optional, Dict, Any

import psycopg2.extras
from dynamic_analyst.storage.postgis.db_pool import get_db_connection

_TABLE_READY = False


def _conn():
    return get_db_connection()


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.users (
                user_id       TEXT PRIMARY KEY,
                provider      TEXT NOT NULL,
                provider_id   TEXT NOT NULL,
                email         TEXT,
                name          TEXT,
                avatar_url    TEXT,
                password_hash TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_login_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(provider, provider_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS users_provider_idx ON public.users (provider, provider_id)"
        )
        # Add password_hash column if table already existed without it
        cur.execute("""
            ALTER TABLE public.users ADD COLUMN IF NOT EXISTS password_hash TEXT
        """)
    _TABLE_READY = True


def upsert_user(
    provider: str,
    provider_id: str,
    email: Optional[str],
    name: Optional[str],
    avatar_url: Optional[str],
) -> Dict[str, Any]:
    _ensure_table()
    user_id = f"{provider}:{provider_id}"
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO public.users (user_id, provider, provider_id, email, name, avatar_url, last_login_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (provider, provider_id)
            DO UPDATE SET
                email         = EXCLUDED.email,
                name          = EXCLUDED.name,
                avatar_url    = EXCLUDED.avatar_url,
                last_login_at = NOW()
            RETURNING *
        """, (user_id, provider, provider_id, email, name, avatar_url))
        row = cur.fetchone()
    return dict(row)


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    _ensure_table()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM public.users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Look up a user by email where provider='email'."""
    _ensure_table()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM public.users WHERE provider = 'email' AND email = %s",
            (email,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def create_email_user(
    email: str,
    name: str,
    password_hash: str,
) -> Dict[str, Any]:
    """Create a new user with email/password auth."""
    _ensure_table()
    user_id = f"email:{email}"
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO public.users
                (user_id, provider, provider_id, email, name, password_hash, last_login_at)
            VALUES (%s, 'email', %s, %s, %s, %s, NOW())
            RETURNING *
        """, (user_id, email, email, name, password_hash))
        row = cur.fetchone()
    return dict(row)


def update_last_login(user_id: str) -> None:
    """Update the last_login_at timestamp."""
    _ensure_table()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE public.users SET last_login_at = NOW() WHERE user_id = %s",
            (user_id,),
        )

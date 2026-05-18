from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from psycopg2.extensions import connection as PgConnection
from psycopg2.pool import ThreadedConnectionPool

from ...config import POSTGIS_DSN

logger = logging.getLogger("adk_server")

_POOL_LOCK = threading.Lock()
_POOL: ThreadedConnectionPool | None = None
_POOL_PID: int | None = None


def _pool_size(var_name: str, default_value: int) -> int:
    raw = os.getenv(var_name, str(default_value)).strip()
    try:
        return max(1, int(raw))
    except Exception:
        logger.warning("Invalid %s=%r; using default=%s", var_name, raw, default_value)
        return default_value


def _pool_limits() -> tuple[int, int]:
    min_conn = _pool_size("POSTGIS_POOL_MIN_CONN", 1)
    max_conn = _pool_size("POSTGIS_POOL_MAX_CONN", 20)
    if max_conn < min_conn:
        logger.warning(
            "POSTGIS_POOL_MAX_CONN (%s) < POSTGIS_POOL_MIN_CONN (%s); clamping max to min",
            max_conn,
            min_conn,
        )
        max_conn = min_conn
    return min_conn, max_conn


def _build_pool() -> ThreadedConnectionPool:
    min_conn, max_conn = _pool_limits()
    logger.info("Initializing PostGIS connection pool (min=%s, max=%s)", min_conn, max_conn)
    return ThreadedConnectionPool(minconn=min_conn, maxconn=max_conn, dsn=POSTGIS_DSN)


def _pool_for_process() -> ThreadedConnectionPool:
    global _POOL, _POOL_PID
    pid = os.getpid()
    with _POOL_LOCK:
        if _POOL is not None and _POOL_PID != pid:
            # If a process fork happened, drop inherited pool handles and rebuild.
            try:
                _POOL.closeall()
            except Exception:
                pass
            _POOL = None
            _POOL_PID = None
        if _POOL is None:
            _POOL = _build_pool()
            _POOL_PID = pid
        return _POOL


def close_pool() -> None:
    global _POOL, _POOL_PID
    with _POOL_LOCK:
        if _POOL is None:
            return
        try:
            _POOL.closeall()
            logger.info("Closed PostGIS connection pool")
        except Exception as exc:
            logger.warning("Failed closing PostGIS pool cleanly: %s", exc)
        finally:
            _POOL = None
            _POOL_PID = None


def get_pool_stats() -> dict[str, int | float | bool | None]:
    min_conn, max_conn = _pool_limits()
    with _POOL_LOCK:
        pool = _POOL
        pid = _POOL_PID
        if pool is None:
            used = 0
            idle = 0
        else:
            used = len(getattr(pool, "_used", {}) or ())
            idle = len(getattr(pool, "_pool", []) or ())
    total = used + idle
    utilization = (float(used) / float(max_conn)) if max_conn > 0 else 0.0
    return {
        "initialized": pool is not None,
        "pid": pid,
        "min_conn": min_conn,
        "max_conn": max_conn,
        "in_use": used,
        "idle": idle,
        "total": total,
        "utilization": round(utilization, 4),
        "utilization_pct": round(utilization * 100.0, 1),
        "at_capacity": bool(max_conn > 0 and used >= max_conn),
    }


def ping_db(statement_timeout_ms: int = 1500) -> dict[str, int | float | bool | str | None]:
    timeout_ms = max(100, int(statement_timeout_ms))
    started = time.perf_counter()
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))
            cur.execute("SELECT 1")
            row = cur.fetchone()
            ok = bool(row and row[0] == 1)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "statement_timeout_ms": timeout_ms,
            "error": None if ok else "unexpected_ping_result",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "latency_ms": round(latency_ms, 1),
            "statement_timeout_ms": timeout_ms,
            "error": f"{type(exc).__name__}: {exc}",
        }


@contextmanager
def get_db_connection() -> Iterator[PgConnection]:
    pool = _pool_for_process()
    conn = pool.getconn()
    close_conn = bool(getattr(conn, "closed", 0))
    try:
        conn.autocommit = False
        yield conn
        if conn.closed:
            close_conn = True
        else:
            conn.commit()
    except Exception:
        if conn.closed:
            close_conn = True
        else:
            try:
                conn.rollback()
            except Exception:
                close_conn = True
        raise
    finally:
        try:
            if not conn.closed:
                conn.autocommit = False
        except Exception:
            close_conn = True
        try:
            pool.putconn(conn, close=close_conn or bool(conn.closed))
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


atexit.register(close_pool)

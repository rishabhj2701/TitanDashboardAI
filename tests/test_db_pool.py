from __future__ import annotations

from contextlib import contextmanager

from dynamic_analyst.storage.postgis import db_pool


def test_get_pool_stats_without_initialized_pool(monkeypatch):
    monkeypatch.setenv("POSTGIS_POOL_MIN_CONN", "2")
    monkeypatch.setenv("POSTGIS_POOL_MAX_CONN", "8")
    monkeypatch.setattr(db_pool, "_POOL", None)
    monkeypatch.setattr(db_pool, "_POOL_PID", None)

    stats = db_pool.get_pool_stats()

    assert stats["initialized"] is False
    assert stats["min_conn"] == 2
    assert stats["max_conn"] == 8
    assert stats["in_use"] == 0
    assert stats["idle"] == 0
    assert stats["total"] == 0
    assert stats["at_capacity"] is False


def test_ping_db_success(monkeypatch):
    class FakeCursor:
        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_get_db_connection():
        yield FakeConn()

    monkeypatch.setattr(db_pool, "get_db_connection", fake_get_db_connection)

    result = db_pool.ping_db(300)

    assert result["ok"] is True
    assert result["statement_timeout_ms"] == 300
    assert result["error"] is None


def test_ping_db_failure(monkeypatch):
    class FakeCursor:
        def execute(self, _query, _params=None):
            raise RuntimeError("db down")

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_get_db_connection():
        yield FakeConn()

    monkeypatch.setattr(db_pool, "get_db_connection", fake_get_db_connection)

    result = db_pool.ping_db(500)

    assert result["ok"] is False
    assert result["statement_timeout_ms"] == 500
    assert "RuntimeError" in str(result["error"])

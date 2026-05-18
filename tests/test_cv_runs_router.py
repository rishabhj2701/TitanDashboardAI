import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.routers import cv_runs


class FakeCursor:
    def __init__(
        self,
        fetchone_values=None,
        fetchall_values=None,
        rowcount_values=None,
        on_execute=None,
    ):
        self._fetchone_values = list(fetchone_values or [])
        self._fetchall_values = list(fetchall_values or [])
        self._rowcount_values = list(rowcount_values or [])
        self._on_execute = on_execute
        self.rowcount = 0
        self.exec_count = 0

    def execute(self, query, params=None):
        self.exec_count += 1
        if self._rowcount_values:
            self.rowcount = self._rowcount_values.pop(0)
        if self._on_execute:
            self._on_execute(self.exec_count, query, params)

    def fetchone(self):
        if self._fetchone_values:
            return self._fetchone_values.pop(0)
        return None

    def fetchall(self):
        if self._fetchall_values:
            return self._fetchall_values.pop(0)
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, cursor, dbname="traffic"):
        self._cursor = cursor
        self._dbname = dbname

    def cursor(self, cursor_factory=None):
        return self._cursor

    def get_dsn_parameters(self):
        return {"dbname": self._dbname}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(cv_runs.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache_clearer():
    cv_runs.set_cv_cache_clearer(None)
    yield
    cv_runs.set_cv_cache_clearer(None)


def test_list_cv_runs_requires_session_header():
    client = _client()
    response = client.get("/api/cv/runs")
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_list_cv_runs_empty_when_cv_run_config_missing(monkeypatch):
    cursor = FakeCursor(fetchone_values=[{"rel": "cv_runs"}], fetchall_values=[[]])
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", lambda: FakeConn(cursor))
    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs, "_resolve_active_run_id", lambda _cur, _uid: None)

    client = _client()
    response = client.get("/api/cv/runs", headers={"X-Session-Id": "sess_1"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "active_run_id": None, "runs": []}


def test_list_cv_runs_returns_derived_fields(monkeypatch):
    cursor = FakeCursor(
        fetchone_values=[
            {"rel": "cv_runs"},
        ],
        fetchall_values=[
            [
                {
                    "run_id": "run_1",
                    "schema_name": "cv_schema_a",
                    "created_at": "2026-01-01T00:00:00Z",
                    "display_name": "January Window",
                    "season_tag": "Winter",
                    "state_code": "MO",
                    "point_count": 120,
                    "ts_start": "2026-01-01T00:00:00Z",
                    "ts_end": "2026-01-07T00:00:00Z",
                    "is_visible": True,
                },
                {
                    "run_id": "run_2",
                    "schema_name": "cv_schema_b",
                    "created_at": "2026-02-01T00:00:00Z",
                    "display_name": "February Window",
                    "season_tag": "Winter",
                    "state_code": "",
                    "point_count": 42,
                    "ts_start": "2026-02-01T00:00:00Z",
                    "ts_end": "2026-02-03T00:00:00Z",
                    "is_visible": False,
                },
            ]
        ],
    )
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", lambda: FakeConn(cursor))
    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs, "_resolve_active_run_id", lambda _cur, _uid: "run_1")

    client = _client()
    response = client.get("/api/cv/runs", headers={"X-Session-Id": "sess_2"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["active_run_id"] == "run_1"
    assert len(payload["runs"]) == 2
    assert payload["runs"][0]["is_active"] is True
    assert payload["runs"][0]["is_selectable"] is True
    assert payload["runs"][0]["region"] == "MO"
    assert payload["runs"][0]["states"] == ["MO"]
    assert payload["runs"][1]["is_active"] is False
    assert payload["runs"][1]["states"] == []


def test_get_active_cv_run_returns_none(monkeypatch):
    cursor = FakeCursor(fetchone_values=[])
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", lambda: FakeConn(cursor))
    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs, "_resolve_active_run_id", lambda _cur, _uid: None)

    client = _client()
    response = client.get("/api/cv/active-run", headers={"X-Session-Id": "sess_3"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "active_run": None}


def test_set_active_cv_run_requires_nonempty_id():
    client = _client()
    response = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sess_4"},
        json={"run_id": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "run_id is required."


def test_set_active_cv_run_success_clears_caches(monkeypatch):
    cleared = {"count": 0}

    def _clear():
        cleared["count"] += 1

    cv_runs.set_cv_cache_clearer(_clear)
    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs, "_ensure_user_cv_run_config", lambda _cur: None)

    cursor = FakeCursor(
        fetchone_values=[
            {
                "run_id": "run_5",
                "schema_name": "cv_schema_5",
                "created_at": "2026-02-10T00:00:00Z",
            }
        ],
        rowcount_values=[0, 0, 0, 1],
    )
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", lambda: FakeConn(cursor))

    client = _client()
    response = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sess_5"},
        json={"run_id": "run_5"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["active_run"]["run_id"] == "run_5"
    assert payload["active_run"]["schema_name"] == "cv_schema_5"
    assert payload["execution_mode"] == "schema_qualified"
    assert payload["warnings"] == []
    assert payload["views_repointed"] is False
    assert cleared["count"] == 1


def test_set_active_cv_run_uses_user_scope_table(monkeypatch):
    queries: list[str] = []

    def _on_execute(_idx, query, _params):
        queries.append(str(query))

    cursor = FakeCursor(
        fetchone_values=[
            {
                "run_id": "run_user",
                "schema_name": "cv_schema_u",
                "created_at": "2026-02-10T00:00:00Z",
            }
        ],
        rowcount_values=[0, 0, 0, 1],
        on_execute=_on_execute,
    )
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", lambda: FakeConn(cursor))
    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs, "_active_user_id", lambda: "user_a")
    monkeypatch.setattr(cv_runs, "_ensure_user_cv_run_config", lambda _cur: None)

    client = _client()
    response = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sess_user"},
        json={"run_id": "run_user"},
    )

    assert response.status_code == 200
    joined = "\n".join(queries).lower()
    expected_table = cv_runs.APP_USER_CV_RUN_CONFIG.lower()
    assert f"update {expected_table}" in joined
    assert f"insert into {expected_table}" not in joined
    assert "update public.cv_run_config" not in joined


def test_set_active_cv_run_returns_409_on_lock_timeout(monkeypatch):
    class FakeLockError(Exception):
        def __init__(self, message, pgcode):
            super().__init__(message)
            self.pgcode = pgcode

    def _raise_lock(_idx, _query, _params):
        raise FakeLockError("locked", "55P03")

    monkeypatch.setattr(cv_runs, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_runs.psycopg2, "Error", FakeLockError)
    monkeypatch.setattr(
        cv_runs.postgis_store,
        "_conn",
        lambda: FakeConn(FakeCursor(on_execute=_raise_lock)),
    )

    client = _client()
    response = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sess_6"},
        json={"run_id": "run_6"},
    )

    assert response.status_code == 409
    assert "database lock timeout" in response.json()["detail"].lower()

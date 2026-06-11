from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import data_quality


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(data_quality.router)
    return TestClient(app)


class _FakeCursor:
    def __init__(self, fetchone_values=None, on_execute=None):
        self._fetchone_values = list(fetchone_values or [])
        self._on_execute = on_execute

    def execute(self, query, params=None):
        if self._on_execute:
            self._on_execute(query, params)

    def fetchone(self):
        if self._fetchone_values:
            return self._fetchone_values.pop(0)
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_cv_summary_stats_uses_crash_only_count_and_safe_speed_cast(monkeypatch):
    executed_sql = []
    activated = {"sid": None}

    def _capture_sql(query, _params=None):
        executed_sql.append(str(query).lower())

    cursor = _FakeCursor(
        fetchone_values=[
            {"total": 10},  # cv count
            {"avg_speed": 45.5, "max_speed": 80.0},  # speed stats
            {"total": 3},  # crash count
            {"rel": None},  # cv_hard_brake table absent
        ],
        on_execute=_capture_sql,
    )
    monkeypatch.setattr(data_quality, "set_active_session", lambda sid: activated.update({"sid": sid}))
    monkeypatch.setattr(data_quality.postgis_store, "_conn", lambda: _FakeConn(cursor))

    client = _client()
    response = client.get("/api/cv/summary-stats", headers={"X-Session-Id": "sess_dq"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["cvPoints"] == 10
    assert payload["crashes"] == 3
    assert payload["hardBraking"] == 0
    assert payload["total"] == 13
    assert payload["avgSpeed"] == 45.5
    assert payload["maxSpeed"] == 80.0
    assert activated["sid"] == "sess_dq"

    speed_sql = executed_sql[1]
    assert "avg(case" in speed_sql
    assert "btrim" in speed_sql
    assert "~ '^[+-]?([0-9]+(\\.[0-9]+)?|\\.[0-9]+)$'" in speed_sql

    crash_sql = executed_sql[2]
    assert "from app_data.events e" in crash_sql
    assert "join app_data.datasets d on d.dataset_id = e.dataset_id" in crash_sql
    assert "where lower(coalesce(d.entity_type, '')) = 'crash'" in crash_sql


def test_data_quality_preserves_200_error_payload_when_strict_mode_disabled(monkeypatch):
    monkeypatch.delenv("DATA_QUALITY_STRICT_ERRORS", raising=False)
    monkeypatch.setattr(data_quality, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(
        data_quality.postgis_store,
        "_conn",
        lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    client = _client()
    response = client.get("/api/cv/summary-stats", headers={"X-Session-Id": "sess_dq_default"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["cvPoints"] == 0


def test_data_quality_returns_500_when_strict_mode_enabled(monkeypatch):
    monkeypatch.setenv("DATA_QUALITY_STRICT_ERRORS", "1")
    monkeypatch.setattr(data_quality, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(
        data_quality.postgis_store,
        "_conn",
        lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    client = _client()
    response = client.get("/api/cv/summary-stats", headers={"X-Session-Id": "sess_dq_strict"})

    assert response.status_code == 500
    assert "cv summary stats failed" in response.json()["detail"].lower()

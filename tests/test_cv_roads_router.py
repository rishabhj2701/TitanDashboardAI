from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import cv_roads


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(cv_roads.router)
    return TestClient(app)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _sql, _params=None):
        return None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._rows)


def test_cv_roads_requires_session_header():
    client = _client()
    response = client.get("/api/cv/roads")
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_cv_roads_returns_distinct_roads(monkeypatch):
    activated = {"sid": None}
    monkeypatch.setattr(cv_roads, "set_active_session", lambda sid: activated.update({"sid": sid}))
    monkeypatch.setattr(cv_roads, "_resolve_cv_points_relation", lambda _cur: "cv_points")
    monkeypatch.setattr(cv_roads, "_attrs_road_name_expr", lambda _attrs: "road_name")
    monkeypatch.setattr(
        cv_roads.postgis_store,
        "_conn",
        lambda: _FakeConn([{"road_name": "I-70"}, {"road_name": "I-44"}, {"road_name": None}]),
    )

    client = _client()
    response = client.get("/api/cv/roads", headers={"X-Session-Id": "sess_cv_roads"})
    assert response.status_code == 200
    assert activated["sid"] == "sess_cv_roads"
    assert response.json() == {"status": "success", "count": 2, "roads": ["I-70", "I-44"]}


def test_cv_roads_returns_500_on_failure(monkeypatch):
    monkeypatch.setattr(cv_roads, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_roads, "_resolve_cv_points_relation", lambda _cur: None)
    monkeypatch.setattr(cv_roads.postgis_store, "_conn", lambda: _FakeConn([]))

    client = _client()
    response = client.get("/api/cv/roads", headers={"X-Session-Id": "sess_fail"})
    assert response.status_code == 500
    assert "cv_points table not found" in response.json()["detail"]

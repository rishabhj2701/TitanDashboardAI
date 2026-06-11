from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import roads_bbox


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(roads_bbox.router)
    return TestClient(app)


class _FakeCursor:
    def __init__(self, bbox=(-90.2, 38.5, -90.1, 38.7)):
        self._last_sql = ""
        self._last_params = None
        self._bbox = bbox

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._last_sql = str(sql)
        self._last_params = params

    def fetchone(self):
        sql = self._last_sql
        params = self._last_params
        if "SELECT to_regclass" in sql:
            relation = params[0] if isinstance(params, (list, tuple)) and params else None
            if relation == "public.cv_road_stats_mv":
                return ("public.cv_road_stats_mv",)
            return (None,)
        if "FROM pg_attribute" in sql:
            column = params[1] if isinstance(params, (list, tuple)) and len(params) > 1 else None
            if column == "geom_3857":
                return (1,)
            return None
        if "SELECT ST_XMin(ext)" in sql:
            return self._bbox
        return None


class _FakeConn:
    def __init__(self, bbox=(-90.2, 38.5, -90.1, 38.7)):
        self._bbox = bbox

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self._bbox)


def test_roads_bbox_returns_bbox_payload(monkeypatch):
    roads_bbox.clear_roads_bbox_cache()
    monkeypatch.setattr(roads_bbox.postgis_store, "_conn", lambda: _FakeConn())
    client = _client()

    response = client.get("/api/roads/bbox")
    assert response.status_code == 200
    assert response.json() == {"bbox": {"minLon": -90.2, "minLat": 38.5, "maxLon": -90.1, "maxLat": 38.7}}


def test_roads_bbox_uses_cache_and_clear_removes_cache(monkeypatch):
    roads_bbox.clear_roads_bbox_cache()
    monkeypatch.setattr(roads_bbox.postgis_store, "_conn", lambda: _FakeConn())
    client = _client()

    first = client.get("/api/roads/bbox")
    assert first.status_code == 200
    assert first.json()["bbox"]["minLon"] == -90.2

    def _fail_conn():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(roads_bbox.postgis_store, "_conn", _fail_conn)

    # Served from cache
    cached = client.get("/api/roads/bbox")
    assert cached.status_code == 200
    assert cached.json()["bbox"]["minLon"] == -90.2

    roads_bbox.clear_roads_bbox_cache()

    # Cache cleared, so failure path returns bbox None
    after_clear = client.get("/api/roads/bbox")
    assert after_clear.status_code == 200
    assert after_clear.json() == {"bbox": None}

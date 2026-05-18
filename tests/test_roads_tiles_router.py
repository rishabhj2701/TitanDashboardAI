from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import roads_tiles


def _client(raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(roads_tiles.router)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


class _FakeCursor:
    def __init__(self, has_mvt_function: bool, tile: bytes):
        self._has_mvt_function = has_mvt_function
        self._tile = tile
        self._last_sql = ""
        self._last_params = None

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
        if "to_regprocedure" in sql:
            return ("public.get_cv_roads_mvt",) if self._has_mvt_function else (None,)
        if "SELECT public.get_cv_roads_mvt" in sql:
            return (self._tile,)
        if "SELECT to_regclass" in sql:
            relation = params[0] if isinstance(params, (list, tuple)) and params else None
            if relation == "public.cv_road_stats_mv":
                return ("public.cv_road_stats_mv",)
            return (None,)
        if "FROM pg_attribute" in sql:
            return (1,)
        if "SELECT ST_AsMVT" in sql:
            return (self._tile,)
        return None


class _FakeConn:
    def __init__(self, has_mvt_function: bool, tile: bytes):
        self._cursor = _FakeCursor(has_mvt_function=has_mvt_function, tile=tile)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


def test_roads_tiles_requires_dataset_when_mvt_function_missing(monkeypatch):
    roads_tiles.clear_road_tiles_cache()
    monkeypatch.setattr(roads_tiles, "_latest_cv_dataset_id", lambda: None)
    monkeypatch.setattr(roads_tiles.postgis_store, "_conn", lambda: _FakeConn(has_mvt_function=False, tile=b""))

    client = _client(raise_server_exceptions=False)
    response = client.get("/tiles/roads/7/19/48.mvt")
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing dataset_id"


def test_roads_tiles_uses_cache_and_clear(monkeypatch):
    roads_tiles.clear_road_tiles_cache()
    monkeypatch.setattr(roads_tiles.postgis_store, "_conn", lambda: _FakeConn(has_mvt_function=True, tile=b"tile-data"))

    client = _client(raise_server_exceptions=False)
    first = client.get("/tiles/roads/9/150/192.mvt?dataset_id=cv_ds")
    assert first.status_code == 200
    assert first.content == b"tile-data"

    def _fail_conn():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(roads_tiles.postgis_store, "_conn", _fail_conn)
    cached = client.get("/tiles/roads/9/150/192.mvt?dataset_id=cv_ds")
    assert cached.status_code == 200
    assert cached.content == b"tile-data"

    roads_tiles.clear_road_tiles_cache()
    after_clear = client.get("/tiles/roads/9/150/192.mvt?dataset_id=cv_ds")
    assert after_clear.status_code == 500


def test_roads_tiles_fallback_sql_path_returns_tile(monkeypatch):
    roads_tiles.clear_road_tiles_cache()
    monkeypatch.setattr(roads_tiles.postgis_store, "_conn", lambda: _FakeConn(has_mvt_function=False, tile=b"sql-tile"))

    client = _client()
    response = client.get("/tiles/roads/11/320/450.mvt?dataset_id=cv_ds")
    assert response.status_code == 200
    assert response.content == b"sql-tile"

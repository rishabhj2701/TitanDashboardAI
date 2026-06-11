from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import cv_query


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(cv_query.router)
    return TestClient(app)


class _QueryCursor:
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


class _QueryConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return _QueryCursor(self._rows)


class _AggregateCursor:
    def __init__(self, has_source=True, source_relname="cv_road_stats_mv", valid_candidates=None):
        self._has_source = has_source
        self._source_relname = source_relname
        self._valid_candidates = set(valid_candidates or {"cv_road_stats_mv", "public.cv_road_stats_mv"})
        self._one = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        text = str(sql)
        self._one = None
        self._rows = []

        if "SELECT to_regclass(%s)::text AS relname" in text:
            candidate = (params or [None])[0]
            if self._has_source and candidate in self._valid_candidates:
                self._one = {"relname": self._source_relname}
            else:
                self._one = {"relname": None}
            return

        if "FROM pg_attribute" in text:
            col = (params or [None, None])[1]
            present = {
                "geom",
                "road_segment_id",
                "point_count",
                "avg_speed_mph",
                "min_speed_mph",
                "max_speed_mph",
                "speed_limit_mode",
                "start_ts",
                "end_ts",
                "road_name",
                "dataset_id",
            }
            self._one = (1,) if col in present else None
            return

        if "ST_AsGeoJSON" in text and f"FROM {self._source_relname}" in text:
            self._rows = [
                {
                    "road_segment_id": "seg_1",
                    "road_name": "I-70",
                    "geom": '{"type":"LineString","coordinates":[[-90.1,38.6],[-90.0,38.7]]}',
                    "avg_speed": 51.4,
                    "speed_limit_p50": None,
                    "speed_limit_p90": None,
                    "speed_limit_mode": 55.0,
                    "min_speed": 41.0,
                    "max_speed": 66.0,
                    "start_ts": datetime(2025, 7, 13, 8, 0, tzinfo=timezone.utc),
                    "end_ts": datetime(2025, 7, 13, 9, 0, tzinfo=timezone.utc),
                    "point_count": 212,
                }
            ]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _AggregateConn:
    def __init__(self, has_source=True, source_relname="cv_road_stats_mv", valid_candidates=None):
        self._cursor = _AggregateCursor(
            has_source=has_source,
            source_relname=source_relname,
            valid_candidates=valid_candidates,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


def test_cv_query_requires_session_header():
    client = _client()
    response = client.post("/api/cv/query", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_cv_query_returns_rows(monkeypatch):
    activated = {"sid": None}
    monkeypatch.setattr(cv_query, "set_active_session", lambda sid: activated.update({"sid": sid}))
    monkeypatch.setattr(
        cv_query.postgis_store,
        "_conn",
        lambda: _QueryConn(
            [
                {
                    "id": "1",
                    "type": "Vehicle",
                    "longitude": -90.1,
                    "latitude": 38.6,
                    "timestamp": "2025-07-13T10:00:00Z",
                    "speed": 63.5,
                    "bearing": 0,
                    "acc_x": 1.2,
                    "acc_y": -0.4,
                    "speedLimit": 55,
                    "roadName": "I-70",
                    "vehicle_id": "veh-1",
                    "county": "St. Louis",
                    "func_class": None,
                    "original_road": None,
                    "road_segment_id": "seg_1",
                    "road_dist_m": 4.0,
                    "road_conf": 0.98,
                }
            ]
        ),
    )

    client = _client()
    response = client.post(
        "/api/cv/query",
        headers={"X-Session-Id": "sess_cv_query"},
        json={"limit": 100},
    )
    body = response.json()

    assert response.status_code == 200
    assert activated["sid"] == "sess_cv_query"
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["data"][0]["acceleration"] == {"x": 1.2, "y": -0.4}
    assert body["data"][0]["speedLimit"] == 55.0


def test_cv_query_returns_500_on_failure(monkeypatch):
    monkeypatch.setattr(cv_query, "set_active_session", lambda _sid: None)

    def _fail_conn():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(cv_query.postgis_store, "_conn", _fail_conn)
    client = _client()

    response = client.post("/api/cv/query", headers={"X-Session-Id": "sess_fail"}, json={})
    assert response.status_code == 500
    assert "db unavailable" in response.json()["detail"]


def test_cv_aggregate_roads_requires_session_header():
    client = _client()
    response = client.post("/api/cv/aggregate-roads", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_cv_aggregate_roads_returns_geojson(monkeypatch):
    monkeypatch.setattr(cv_query, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_query, "_latest_cv_dataset_id", lambda: "cv_ds_latest")
    monkeypatch.setattr(cv_query.postgis_store, "_conn", lambda: _AggregateConn(has_source=True))

    client = _client()
    response = client.post("/api/cv/aggregate-roads", headers={"X-Session-Id": "sess_agg"}, json={})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["dataset_id"] == "cv_ds_latest"
    assert body["count"] == 1
    feature = body["geojson"]["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["road_name"] == "I-70"
    assert feature["properties"]["point_count"] == 212
    assert feature["properties"]["avg_speed_mph"] == 51.4


def test_cv_aggregate_roads_returns_500_when_source_missing(monkeypatch):
    monkeypatch.setattr(cv_query, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_query, "_latest_cv_dataset_id", lambda: "cv_ds_latest")
    monkeypatch.setattr(cv_query.postgis_store, "_conn", lambda: _AggregateConn(has_source=False))

    client = _client()
    response = client.post("/api/cv/aggregate-roads", headers={"X-Session-Id": "sess_agg"}, json={})
    assert response.status_code == 500
    assert "No road aggregate source table found" in response.json()["detail"]


def test_cv_aggregate_roads_resolves_schema_qualified_source(monkeypatch):
    monkeypatch.setattr(cv_query, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(cv_query, "_latest_cv_dataset_id", lambda: "cv_ds_latest")
    monkeypatch.setattr(
        cv_query,
        "_cv_relation_candidates",
        lambda _conn, rel: [f"cv_active.{rel}", f"public.{rel}", rel],
    )
    monkeypatch.setattr(
        cv_query.postgis_store,
        "_conn",
        lambda: _AggregateConn(
            has_source=True,
            source_relname="cv_active.cv_road_stats_mv",
            valid_candidates={"cv_active.cv_road_stats_mv"},
        ),
    )

    client = _client()
    response = client.post("/api/cv/aggregate-roads", headers={"X-Session-Id": "sess_agg"}, json={})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["geojson"]["features"][0]["properties"]["road_name"] == "I-70"


def test_road_search_patterns_expands_route_aliases():
    patterns = cv_query._road_search_patterns("I-70")
    assert "I-70" in patterns
    assert "I 70" in patterns
    assert "INTERSTATE 70" in patterns

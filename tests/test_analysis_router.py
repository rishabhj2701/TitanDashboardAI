import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.routers import analysis


def _client(raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(analysis.router)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_crash_analyze_requires_session_header():
    client = _client()
    response = client.post("/api/crash/analyze", json={"crash_lat": 38.6, "crash_lon": -90.2})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_area_analyze_requires_session_header():
    client = _client()
    response = client.post(
        "/api/area/analyze",
        json={
            "polygon": {
                "type": "Polygon",
                "coordinates": [[[-90.2, 38.6], [-90.1, 38.6], [-90.1, 38.7], [-90.2, 38.7], [-90.2, 38.6]]],
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_workzone_analyze_requires_session_header():
    client = _client()
    response = client.post("/api/workzone/analyze", json={"workzone_id": "wz_1"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_crash_analyze_returns_500_when_db_fails(monkeypatch):
    monkeypatch.setattr(analysis, "_latest_cv_dataset_id", lambda: "cv_ds")

    def _fail_conn():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(analysis.postgis_store, "_conn", _fail_conn)
    client = _client(raise_server_exceptions=False)

    response = client.post(
        "/api/crash/analyze",
        headers={"X-Session-Id": "sess_analysis"},
        json={"crash_lat": 38.6, "crash_lon": -90.2},
    )
    assert response.status_code == 500
    assert "db unavailable" in response.json()["detail"]


def test_area_analysis_mode_normalization():
    assert analysis._normalize_area_analysis_mode("auto") == "auto"
    assert analysis._normalize_area_analysis_mode("DETAIL") == "detail"
    assert analysis._normalize_area_analysis_mode(" Aggregate ") == "aggregate"
    with pytest.raises(ValueError):
        analysis._normalize_area_analysis_mode("huge")


def test_area_analysis_cache_roundtrip():
    key = "test_area_cache_key"
    payload = {
        "status": "success",
        "mode": "aggregate",
        "summary": {"points": 42},
    }
    analysis._area_analysis_cache_put(key, payload)
    cached = analysis._area_analysis_cache_get(key)
    assert cached == payload
    assert cached is not payload
    cached["summary"]["points"] = 99
    cached_again = analysis._area_analysis_cache_get(key)
    assert cached_again is not None
    assert cached_again["summary"]["points"] == 42


def test_build_area_analysis_context_contains_geometry_and_important_metrics():
    polygon_json = json.dumps(
        {
            "type": "Polygon",
            "coordinates": [[[-90.2, 38.6], [-90.1, 38.6], [-90.1, 38.7], [-90.2, 38.7], [-90.2, 38.6]]],
        }
    )
    summary = {
        "points": 120,
        "vehicles": 55,
        "crashes": 3,
        "hard_brakes": 9,
        "top_roads": [{"road_name": "I-70", "count": 40}],
        "area_km2": 12.5,
    }
    context = analysis._build_area_analysis_context(
        response_text="Area analysis (polygon)\n- CV points: 120",
        summary=summary,
        mode="aggregate",
        polygon_json=polygon_json,
        cv_dataset_id="cv_ds",
        crash_dataset_id="crash_ds",
        workzone_dataset_id="wz_ds",
    )

    assert context["analysis_type"] == "area"
    assert context["mode"] == "aggregate"
    assert context["important_metrics"]["points"] == 120
    assert context["important_metrics"]["top_roads"][0]["road_name"] == "I-70"
    assert context["geometry"]["type"] == "Polygon"
    assert context["geometry_meta"]["vertex_count"] == 5
    assert context["geometry_meta"]["area_km2"] == 12.5
    assert context["geometry_meta"]["polygon_hash"]


def test_build_crash_analysis_context_compact_shape():
    payload = analysis.CrashAnalyzeRequest(crash_lat=38.6, crash_lon=-90.2)
    context = analysis._build_crash_analysis_context(
        response_text="Crash analysis\n- CV points: 42",
        payload=payload,
        params={"distance_m": 200.0, "window_minutes": 60},
        summary={"points": 42, "vehicles": 21, "avg_speed": 35.2},
        braking={"hard_braking_events": 5, "hard_braking_vehicles": 4},
        road_segment_id="seg_1",
        crash_road_name="I-70",
        workzone_lines_count=2,
        cv_dataset_id="cv_ds",
        workzone_dataset_id="wz_ds",
    )

    assert context["analysis_type"] == "crash"
    assert context["important_metrics"]["distance_m"] == 200.0
    assert context["important_metrics"]["window_minutes"] == 60
    assert context["important_metrics"]["cv_points"] == 42
    assert context["important_metrics"]["hard_braking_events"] == 5
    assert context["important_metrics"]["road_segment_id"] == "seg_1"
    assert context["anchor"]["latitude"] == 38.6
    assert context["anchor"]["longitude"] == -90.2


def test_build_workzone_analysis_context_compact_shape():
    payload = analysis.WorkzoneAnalyzeRequest(workzone_id="wz_1")
    context = analysis._build_workzone_analysis_context(
        response_text="Workzone analysis\n- During workzone window: 100 points",
        payload=payload,
        summary={"with_points": 100, "with_vehicles": 33, "with_avg_speed": 29.3, "spatial_points": 70, "conflated_points": 50},
        braking={"hard_braking_events": 11, "hard_braking_vehicles": 7},
        crash_points_total=4,
        road_segment_id="seg_wz",
        road_name="US-40",
        distance_m=200.0,
        effective_during_start="2025-01-01T00:00:00Z",
        effective_during_end="2025-01-10T00:00:00Z",
        cv_dataset_id="cv_ds",
        crash_dataset_id="crash_ds",
        workzone_dataset_id="wz_ds",
    )

    assert context["analysis_type"] == "workzone"
    assert context["important_metrics"]["cv_points"] == 100
    assert context["important_metrics"]["vehicles"] == 33
    assert context["important_metrics"]["crashes_within_500m"] == 4
    assert context["important_metrics"]["road_segment_id"] == "seg_wz"
    assert context["important_metrics"]["road_name"] == "US-40"
    assert context["anchor"]["workzone_id"] == "wz_1"

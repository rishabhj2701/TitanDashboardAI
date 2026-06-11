from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import dataset_mappings


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(dataset_mappings.router)
    return TestClient(app)


def test_update_dataset_mapping_requires_session_header():
    client = _client()
    response = client.post("/api/datasets/sample/mapping", json={"fields": {"road": "I-70"}})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_update_dataset_mapping_sanitizes_fields_and_returns_detail(monkeypatch):
    updated = {}
    activated = {"sid": None}

    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda sid: activated.update({"sid": sid}))
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_internal")
    monkeypatch.setattr(dataset_mappings.registry, "get_metadata", lambda _dataset_id: {"ok": True})
    monkeypatch.setattr(
        dataset_mappings.registry,
        "update_metadata",
        lambda dataset_id, patch: updated.update({"dataset_id": dataset_id, "patch": patch}),
    )
    monkeypatch.setattr(
        dataset_mappings,
        "build_dataset_detail",
        lambda dataset_id: {"dataset_id": dataset_id, "detail": "ok"},
    )

    client = _client()
    response = client.post(
        "/api/datasets/sample/mapping",
        headers={"X-Session-Id": "sess_map"},
        json={
            "entity_type": "crash",
            "fields": {
                "road": " I-70 ",
                "blank": "   ",
                "none_value": None,
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"dataset_id": "ds_internal", "detail": "ok"}
    assert activated["sid"] == "sess_map"
    assert updated["dataset_id"] == "ds_internal"
    mapping = updated["patch"]["mapping"]
    assert mapping["entity_type"] == "crash"
    assert mapping["auto"] is False
    assert mapping["fields"] == {"road": "I-70", "blank": None, "none_value": None}
    assert isinstance(mapping["updated_at"], str) and mapping["updated_at"].endswith("Z")


def test_update_dataset_mapping_returns_404_for_missing_dataset(monkeypatch):
    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: None)

    def _missing(_dataset_id):
        raise RuntimeError("not found")

    monkeypatch.setattr(dataset_mappings.registry, "get_metadata", _missing)

    client = _client()
    response = client.post(
        "/api/datasets/unknown/mapping",
        headers={"X-Session-Id": "sess_missing"},
        json={"fields": {"a": "b"}},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_update_dataset_mapping_merges_fields_and_keeps_existing_entity(monkeypatch):
    updated = {}

    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_merge")
    monkeypatch.setattr(
        dataset_mappings.registry,
        "get_metadata",
        lambda _dataset_id: {
            "entity_type": "crash",
            "mapping": {
                "entity_type": "crash",
                "fields": {
                    "event_date": "accident_date",
                    "timestamp": "accident_date+accident_time",
                },
            },
        },
    )
    monkeypatch.setattr(
        dataset_mappings.registry,
        "update_metadata",
        lambda dataset_id, patch: updated.update({"dataset_id": dataset_id, "patch": patch}),
    )
    monkeypatch.setattr(
        dataset_mappings,
        "build_dataset_detail",
        lambda dataset_id: {"dataset_id": dataset_id, "detail": "merged"},
    )

    client = _client()
    response = client.post(
        "/api/datasets/sample/mapping",
        headers={"X-Session-Id": "sess_merge"},
        json={
            "fields": {
                "event_time": " accident_time ",
                "timestamp": "   ",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"dataset_id": "ds_merge", "detail": "merged"}
    assert updated["dataset_id"] == "ds_merge"
    mapping = updated["patch"]["mapping"]
    assert mapping["entity_type"] == "crash"
    assert mapping["fields"] == {
        "event_date": "accident_date",
        "timestamp": None,
        "event_time": "accident_time",
    }


def test_update_queryable_fields_requires_fields_array(monkeypatch):
    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_queryable")
    monkeypatch.setattr(dataset_mappings.registry, "get_metadata", lambda _dataset_id: {"entity_type": "crash"})

    client = _client()
    response = client.post(
        "/api/datasets/sample/queryable-fields",
        headers={"X-Session-Id": "sess_queryable"},
        json={},
    )
    assert response.status_code == 400
    assert "fields" in response.json()["detail"].lower()


def test_update_queryable_fields_normalizes_and_keeps_locked_defaults(monkeypatch):
    updated = {}

    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_queryable")
    monkeypatch.setattr(
        dataset_mappings.registry,
        "get_metadata",
        lambda _dataset_id: {
            "entity_type": "crash",
            "mapping": {"entity_type": "crash", "fields": {}},
            "columns": [
                "accident_date",
                "accident_time",
                "landed_latitude",
                "landed_longitude",
                "severity",
            ],
            "stats": {},
        },
    )
    monkeypatch.setattr(
        dataset_mappings.registry,
        "update_metadata",
        lambda dataset_id, patch: updated.update({"dataset_id": dataset_id, "patch": patch}),
    )
    monkeypatch.setattr(
        dataset_mappings,
        "build_dataset_detail",
        lambda dataset_id: {"dataset_id": dataset_id, "detail": "queryable_saved"},
    )

    client = _client()
    response = client.post(
        "/api/datasets/sample/queryable-fields",
        headers={"X-Session-Id": "sess_queryable"},
        json={
            "fields": [
                {"query_name": "Crash Date", "source_column": "accident_date", "enabled": True},
                {"query_name": "Severity Label", "source_column": "severity", "enabled": False},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"dataset_id": "ds_queryable", "detail": "queryable_saved"}
    assert updated["dataset_id"] == "ds_queryable"
    queryable = updated["patch"]["queryable_fields"]
    assert queryable["entity_type"] == "crash"
    assert isinstance(queryable["updated_at"], str) and queryable["updated_at"].endswith("Z")

    fields = queryable["fields"]
    assert any(item["query_name"] == "crash_date" and item["source_column"] == "accident_date" for item in fields)
    locked = {item["query_name"]: item for item in fields if item.get("locked")}
    assert "event_date" in locked
    assert "event_time" in locked
    assert "latitude" in locked
    assert "longitude" in locked
    assert all(item.get("enabled") for item in locked.values())


def test_update_code_mappings_requires_available_attributes(monkeypatch):
    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_1")
    monkeypatch.setattr(dataset_mappings.registry, "get_metadata", lambda _dataset_id: {"stats": {}})
    monkeypatch.setattr(dataset_mappings.postgis_store, "list_codebook_attributes", lambda: [])

    client = _client()
    response = client.post(
        "/api/datasets/sample/code-mappings",
        headers={"X-Session-Id": "sess_code"},
        json={"mappings": {"severity_col": "SEVERITY"}},
    )
    assert response.status_code == 400
    assert "no codebook attributes" in response.json()["detail"].lower()


def test_update_code_mappings_rejects_unknown_attribute(monkeypatch):
    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_2")
    monkeypatch.setattr(dataset_mappings.registry, "get_metadata", lambda _dataset_id: {"stats": {}})
    monkeypatch.setattr(dataset_mappings.postgis_store, "list_codebook_attributes", lambda: ["SEVERITY"])

    client = _client()
    response = client.post(
        "/api/datasets/sample/code-mappings",
        headers={"X-Session-Id": "sess_unknown"},
        json={"mappings": {"severity_col": "road_type"}},
    )
    assert response.status_code == 400
    assert "unknown codebook attribute" in response.json()["detail"].lower()


def test_update_code_mappings_updates_metadata_and_returns_detail(monkeypatch):
    captured = {}
    monkeypatch.setattr(dataset_mappings, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(dataset_mappings.registry, "resolve_dataset_name", lambda _name: "ds_3")
    monkeypatch.setattr(
        dataset_mappings.registry,
        "get_metadata",
        lambda _dataset_id: {
            "stats": {
                "codebook": {
                    "mappings": {
                        "legacy_col": "SEVERITY",
                        "keep_col": {"attr": "TYPE", "method": "auto"},
                    },
                    "candidates": ["legacy_col", "keep_col", "stale_col"],
                }
            }
        },
    )
    monkeypatch.setattr(
        dataset_mappings.registry,
        "update_metadata",
        lambda dataset_id, patch: captured.update({"dataset_id": dataset_id, "patch": patch}),
    )
    monkeypatch.setattr(dataset_mappings.postgis_store, "list_codebook_attributes", lambda: ["SEVERITY", "TYPE"])

    def _distinct(_dataset_id, column, limit=120):
        if column == "legacy_col":
            return ["01", "02"]
        if column == "new_col":
            return ["A", "B", "C"]
        return []

    monkeypatch.setattr(dataset_mappings.postgis_store, "get_dataset_distinct_values", _distinct)

    def _lookup(attrs):
        attr = attrs[0]
        if attr == "SEVERITY":
            return {"SEVERITY": {"1": "Fatal", "2": "Injury"}}
        if attr == "TYPE":
            return {"TYPE": {"A": "Mainline", "B": "Ramp"}}
        return {attr: {}}

    monkeypatch.setattr(dataset_mappings.postgis_store, "get_codebook_map_for_columns", _lookup)
    monkeypatch.setattr(
        dataset_mappings,
        "build_dataset_detail",
        lambda dataset_id: {"dataset_id": dataset_id, "detail": "updated"},
    )

    client = _client()
    response = client.post(
        "/api/datasets/sample/code-mappings",
        headers={"X-Session-Id": "sess_success"},
        json={
            "mappings": {
                "legacy_col": "severity",
                "new_col": "type",
                "stale_col": "",
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"dataset_id": "ds_3", "detail": "updated"}
    assert captured["dataset_id"] == "ds_3"
    codebook = captured["patch"]["codebook"]
    assert codebook["available"] is True
    assert codebook["attributes"] == 2
    assert codebook["matches"] == ["keep_col", "legacy_col", "new_col"]
    assert "stale_col" in codebook["missing"]
    assert codebook["mappings"]["legacy_col"]["attr"] == "SEVERITY"
    assert codebook["mappings"]["legacy_col"]["coverage"] == 1.0
    assert codebook["mappings"]["new_col"]["attr"] == "TYPE"
    assert codebook["mappings"]["new_col"]["mapped_unique_values"] == 2
    assert codebook["mappings"]["new_col"]["unique_values"] == 3

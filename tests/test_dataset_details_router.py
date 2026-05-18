from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import dataset_details


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(dataset_details.router)
    return TestClient(app)


def test_dataset_detail_by_id_returns_service_payload(monkeypatch):
    monkeypatch.setattr(
        dataset_details,
        "build_dataset_detail",
        lambda dataset_id: {"dataset_id": dataset_id, "name": "sample"},
    )
    client = _client()
    response = client.get("/api/datasets-id/ds_123")

    assert response.status_code == 200
    assert response.json() == {"dataset_id": "ds_123", "name": "sample"}


def test_dataset_detail_by_id_returns_404_when_service_fails(monkeypatch):
    def _boom(_dataset_id):
        raise RuntimeError("missing")

    monkeypatch.setattr(dataset_details, "build_dataset_detail", _boom)
    client = _client()
    response = client.get("/api/datasets-id/cv_run:abc")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_dataset_by_id_returns_deleted_counts(monkeypatch):
    monkeypatch.setattr(
        dataset_details.postgis_store,
        "delete_dataset",
        lambda dataset_id: {
            "dataset_id": dataset_id,
            "events_deleted": 4181,
            "datasets_deleted": 1,
            "codebook_deleted": 12,
        },
    )
    client = _client()
    response = client.delete("/api/datasets-id/crash_abc123")

    assert response.status_code == 200
    assert response.json() == {
        "status": "deleted",
        "dataset_id": "crash_abc123",
        "events_deleted": 4181,
        "datasets_deleted": 1,
        "codebook_deleted": 12,
    }


def test_delete_dataset_by_id_returns_404_when_not_found(monkeypatch):
    monkeypatch.setattr(
        dataset_details.postgis_store,
        "delete_dataset",
        lambda dataset_id: {
            "dataset_id": dataset_id,
            "events_deleted": 0,
            "datasets_deleted": 0,
            "codebook_deleted": 0,
        },
    )
    client = _client()
    response = client.delete("/api/datasets-id/missing_ds")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

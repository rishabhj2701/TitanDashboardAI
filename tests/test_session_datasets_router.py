from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.routers import session_datasets


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(session_datasets.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user"}
    return TestClient(app)


def test_list_datasets_returns_store_rows(monkeypatch):
    monkeypatch.setattr(
        session_datasets,
        "list_session_datasets",
        lambda: [{"dataset_id": "cv_1", "name": "traffic"}],
    )
    client = _client()

    response = client.get("/api/datasets")
    assert response.status_code == 200
    assert response.json() == {"datasets": [{"dataset_id": "cv_1", "name": "traffic"}]}


def test_list_datasets_falls_back_to_empty_list(monkeypatch):
    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(session_datasets, "list_session_datasets", _boom)
    client = _client()

    response = client.get("/api/datasets")
    assert response.status_code == 200
    assert response.json() == {"datasets": []}

import ipaddress

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import uploads as uploads_service
from backend.auth.dependencies import get_current_user
from backend.routers import uploads as uploads_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(uploads_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user"}
    return TestClient(app)


def test_upload_requires_session_header():
    client = _client()
    response = client.post(
        "/api/upload",
        files={"file": ("sample.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_upload_empty_file_returns_400():
    client = _client()
    response = client.post(
        "/api/upload",
        headers={"X-Session-Id": "sess_upload_empty"},
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 400
    assert "empty upload" in response.json()["detail"].lower()


def test_upload_delegates_to_service(monkeypatch):
    captured = {}

    async def _fake_upload_dataset(file, dataset_name=None, entity_type=None, x_session_id=None):
        captured["filename"] = file.filename
        captured["dataset_name"] = dataset_name
        captured["entity_type"] = entity_type
        captured["session_id"] = x_session_id
        return {"status": "success", "dataset_id": "ds_upload_1"}

    monkeypatch.setattr(uploads_router, "upload_dataset", _fake_upload_dataset)
    monkeypatch.setattr(uploads_router, "set_active_session", lambda _sid: None)

    client = _client()
    response = client.post(
        "/api/upload",
        headers={"X-Session-Id": "sess_upload"},
        data={"dataset_name": "test_upload", "entity_type": "crash"},
        files={"file": ("sample.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "dataset_id": "ds_upload_1"}
    assert captured["filename"] == "sample.csv"
    assert captured["dataset_name"] == "test_upload"
    assert captured["entity_type"] == "crash"
    assert captured["session_id"] == "sess_upload"


def test_upload_url_requires_session_header():
    client = _client()
    response = client.post("/api/upload-url", json={"url": "https://example.com/test.csv"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-Session-Id header"


def test_upload_url_invalid_url_returns_400():
    client = _client()
    response = client.post(
        "/api/upload-url",
        headers={"X-Session-Id": "sess_upload_url_invalid"},
        json={"url": "not-a-url"},
    )
    assert response.status_code == 400
    assert "invalid url" in response.json()["detail"].lower()


def test_upload_url_delegates_to_service(monkeypatch):
    captured = {}

    async def _fake_upload_dataset_url(url, dataset_name=None, entity_type=None, x_session_id=None):
        captured["url"] = url
        captured["dataset_name"] = dataset_name
        captured["entity_type"] = entity_type
        captured["session_id"] = x_session_id
        return {"status": "success", "dataset_id": "ds_upload_url_1"}

    monkeypatch.setattr(uploads_router, "upload_dataset_url", _fake_upload_dataset_url)
    monkeypatch.setattr(uploads_router, "set_active_session", lambda _sid: None)

    client = _client()
    response = client.post(
        "/api/upload-url",
        headers={"X-Session-Id": "sess_upload_url"},
        json={"url": "https://example.com/data.csv", "dataset_name": "remote_data", "entity_type": "workzone"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "dataset_id": "ds_upload_url_1"}
    assert captured["url"] == "https://example.com/data.csv"
    assert captured["dataset_name"] == "remote_data"
    assert captured["entity_type"] == "workzone"
    assert captured["session_id"] == "sess_upload_url"


def test_upload_rejects_oversize_when_limit_set(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_MAX_BYTES", 4)

    client = _client()
    response = client.post(
        "/api/upload",
        headers={"X-Session-Id": "sess_upload_limit"},
        files={"file": ("big.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 413
    assert "max size" in response.json()["detail"].lower()


def test_upload_url_rejects_disallowed_scheme(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOWED_SCHEMES", ("https",))

    client = _client()
    response = client.post(
        "/api/upload-url",
        headers={"X-Session-Id": "sess_upload_url_scheme"},
        json={"url": "http://example.com/data.csv"},
    )

    assert response.status_code == 400
    assert "scheme" in response.json()["detail"].lower()
    assert "allowed schemes" in response.json()["detail"].lower()


def test_upload_url_rejects_private_host_when_disallowed(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOW_PRIVATE_NET", False)
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOWED_SCHEMES", ("https",))
    monkeypatch.setattr(
        uploads_service,
        "_resolved_ips",
        lambda _host: [ipaddress.ip_address("127.0.0.1")],
    )

    client = _client()
    response = client.post(
        "/api/upload-url",
        headers={"X-Session-Id": "sess_upload_url_private"},
        json={"url": "https://localhost/data.csv"},
    )

    assert response.status_code == 400
    assert "non-public network address" in response.json()["detail"].lower()


def test_upload_url_rejects_oversize_when_limit_set(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_MAX_BYTES", 4)
    monkeypatch.setattr(uploads_service, "_download_url_bytes", lambda _url: b"12345")

    client = _client()
    response = client.post(
        "/api/upload-url",
        headers={"X-Session-Id": "sess_upload_url_limit"},
        json={"url": "https://example.com/data.csv"},
    )

    assert response.status_code == 413
    assert "max size" in response.json()["detail"].lower()

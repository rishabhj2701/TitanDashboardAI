from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.capabilities import router as capabilities_router


def _add_stub(app: FastAPI, method: str, path: str) -> None:
    async def _handler():
        return {"ok": True}

    method_upper = method.upper()
    if method_upper == "GET":
        app.get(path)(_handler)
    elif method_upper == "POST":
        app.post(path)(_handler)
    elif method_upper == "DELETE":
        app.delete(path)(_handler)
    elif method_upper == "PUT":
        app.put(path)(_handler)
    elif method_upper == "PATCH":
        app.patch(path)(_handler)
    else:
        raise ValueError(f"Unsupported method for test stub: {method}")


def _client_with_routes(routes: list[tuple[str, str]]) -> TestClient:
    app = FastAPI()
    app.include_router(capabilities_router)
    for method, path in routes:
        _add_stub(app, method, path)
    return TestClient(app)


def test_capabilities_marks_chat_available_when_chat_routes_exist():
    client = _client_with_routes(
        [
            ("POST", "/api/chat"),
            ("POST", "/api/chat/clear"),
        ]
    )

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()

    assert payload["version"] == "capabilities-v1"
    assert payload["features"]["chat"]["available"] is True
    assert payload["features"]["ingestion"]["available"] is False
    assert "POST /api/upload" in payload["features"]["ingestion"]["missing_endpoints"]
    assert "POST /api/upload-url" in payload["features"]["ingestion"]["missing_endpoints"]


def test_capabilities_normalizes_path_params_for_roads_tile_routes():
    client = _client_with_routes(
        [
            ("GET", "/tiles/roads/{zoom}/{column}/{row}.mvt"),
            ("GET", "/api/roads/bbox"),
        ]
    )

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()

    assert payload["features"]["roads_tiles"]["available"] is True


def test_capabilities_reports_specific_missing_endpoint():
    client = _client_with_routes([("POST", "/api/upload")])

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()

    assert payload["features"]["ingestion"]["available"] is False
    assert payload["features"]["ingestion"]["missing_endpoints"] == ["POST /api/upload-url"]

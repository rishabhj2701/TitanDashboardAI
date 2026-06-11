from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.routers import chat


def _client(raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user"}
    app.dependency_overrides[chat.get_current_user] = lambda: {"user_id": "test-user"}
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


class _FakeSessionService:
    async def create_session(self, **kwargs):
        return kwargs

    async def delete_session(self, **kwargs):
        return kwargs


class _FakeRunner:
    def __init__(self, response_text: str):
        self.session_service = _FakeSessionService()
        self._response_text = response_text

    async def run_async(self, **kwargs):
        part = SimpleNamespace(text=self._response_text)
        content = SimpleNamespace(parts=[part])
        event = SimpleNamespace(content=content, is_final_response=lambda: True)
        yield event


class _CaptureRunner(_FakeRunner):
    def __init__(self, response_text: str):
        super().__init__(response_text)
        self.last_message_text = ""

    async def run_async(self, **kwargs):
        new_message = kwargs.get("new_message")
        try:
            parts = getattr(new_message, "parts", None) or []
            if parts:
                self.last_message_text = str(getattr(parts[0], "text", "") or "")
        except Exception:
            self.last_message_text = ""
        async for event in super().run_async(**kwargs):
            yield event


class _UsageRunner(_FakeRunner):
    def __init__(
        self,
        response_text: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        model_version: str,
    ):
        super().__init__(response_text)
        self._usage = SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
            total_token_count=total_tokens,
            cached_content_token_count=0,
        )
        self._model_version = model_version

    async def run_async(self, **kwargs):
        part = SimpleNamespace(text=self._response_text)
        content = SimpleNamespace(parts=[part])
        event = SimpleNamespace(
            content=content,
            usage_metadata=self._usage,
            model_version=self._model_version,
            is_final_response=lambda: True,
        )
        yield event


class _ErrorRunner:
    def __init__(self, message: str):
        self.session_service = _FakeSessionService()
        self._message = message

    async def run_async(self, **kwargs):
        raise RuntimeError(self._message)
        yield


def test_chat_endpoint_returns_text_and_chart_payload(monkeypatch):
    monkeypatch.setattr(chat, "runner", _FakeRunner("ok response"))
    monkeypatch.setattr(chat, "_build_dataset_inventory_context_block", lambda limit=24: "")
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=12: [])
    monkeypatch.setattr(chat, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_analysis_context", lambda _sid, limit=6: [])
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: {"chart_payload": [{"type": "bar"}]})

    client = _client()
    response = client.post("/api/chat", json={"message": "hello", "sessionId": "sess_chat"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["sessionId"] == "sess_chat"
    assert payload["responseText"] == "ok response"
    assert payload["chartPayload"] == [{"type": "bar"}]
    assert payload["agentRunMetadata"]["routing"][0]["role"] == "dispatcher"


def test_chat_endpoint_includes_recent_analysis_context_without_polygon_geometry(monkeypatch):
    runner = _CaptureRunner("ok response")
    monkeypatch.setattr(chat, "runner", runner)
    monkeypatch.setattr(chat, "_build_dataset_inventory_context_block", lambda limit=24: "")
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=12: [])
    monkeypatch.setattr(chat, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: None)
    monkeypatch.setattr(
        chat,
        "get_analysis_context",
        lambda _sid, limit=6: [
            {
                "analysis_type": "area",
                "created_at": "2026-02-25T10:00:00+00:00",
                "response_summary": "Polygon analysis summary",
                "important_metrics": {"points": 123, "crashes": 4},
                "dataset_ids": {"cv_dataset_id": "cv_123"},
                "geometry_meta": {"polygon_hash": "abc123"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-90.2, 38.6], [-90.1, 38.6], [-90.1, 38.7], [-90.2, 38.7], [-90.2, 38.6]]],
                },
            }
        ],
    )

    client = _client()
    response = client.post(
        "/api/chat",
        json={"message": "Show crashes within that polygon", "sessionId": "sess_analysis_ctx"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["responseText"] == "ok response"
    assert "[Recent Analysis Context]" in runner.last_message_text
    assert '"geometry_meta": {"polygon_hash": "abc123"}' in runner.last_message_text
    assert "[Latest Polygon Geometry]" not in runner.last_message_text


def test_chat_endpoint_omits_polygon_geometry_when_not_requested(monkeypatch):
    runner = _CaptureRunner("ok response")
    monkeypatch.setattr(chat, "runner", runner)
    monkeypatch.setattr(chat, "_build_dataset_inventory_context_block", lambda limit=24: "")
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=12: [])
    monkeypatch.setattr(chat, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: None)
    monkeypatch.setattr(
        chat,
        "get_analysis_context",
        lambda _sid, limit=6: [
            {
                "analysis_type": "area",
                "created_at": "2026-02-25T10:00:00+00:00",
                "response_summary": "Polygon analysis summary",
                "important_metrics": {"points": 123},
                "geometry_meta": {"polygon_hash": "abc123"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-90.2, 38.6], [-90.1, 38.6], [-90.1, 38.7], [-90.2, 38.7], [-90.2, 38.6]]],
                },
            }
        ],
    )

    client = _client()
    response = client.post(
        "/api/chat",
        json={"message": "Summarize the latest analysis", "sessionId": "sess_analysis_ctx_2"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["responseText"] == "ok response"
    assert "[Recent Analysis Context]" in runner.last_message_text
    assert "[Latest Polygon Geometry]" not in runner.last_message_text


def test_build_dataset_inventory_context_block_labels_static_cv_and_uploaded(monkeypatch):
    monkeypatch.setattr(
        chat,
        "list_session_datasets",
        lambda: [
            {
                "dataset_id": "cv_run:mo_2025_dec_winter",
                "name": "MO Winter 2025",
                "entity_type": "cv",
                "status": "ready",
                "row_count": 25450417,
                "source": "cv_runs",
                "run_id": "mo_2025_dec_winter",
                "schema_name": "cv_mo_202512_winter",
                "state_code": "MO",
                "is_active": True,
                "ts_start": "2025-11-29",
                "ts_end": "2025-12-03",
            },
            {
                "dataset_id": "crash_a5718e015bc1",
                "name": "STL Crash Upload",
                "entity_type": "crash",
                "status": "ready",
                "row_count": 15520,
                "created_at": "2026-02-26T16:00:00+00:00",
                "stats": {
                    "queryable_fields": {
                        "fields": [
                            {"query_name": "road_name", "enabled": True},
                            {"query_name": "city", "enabled": False},
                            {"query_name": "severity", "enabled": True},
                        ]
                    }
                },
            },
        ],
    )

    text = chat._build_dataset_inventory_context_block(limit=24)

    assert "[Dataset Inventory Context]" in text
    assert "dataset_id=cv_run:mo_2025_dec_winter" in text
    assert "origin=backend_static_cv" in text
    assert "dataset_id=crash_a5718e015bc1" in text
    assert "origin=user_uploaded" in text
    assert "queryable_fields=road_name,severity" in text
    assert "Entity types in scope: crash, cv" in text


def test_chat_endpoint_includes_dataset_inventory_context(monkeypatch):
    runner = _CaptureRunner("ok response")
    monkeypatch.setattr(chat, "runner", runner)
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=12: [])
    monkeypatch.setattr(chat, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: None)
    monkeypatch.setattr(chat, "get_analysis_context", lambda _sid, limit=6: [])
    monkeypatch.setattr(
        chat,
        "_build_dataset_inventory_context_block",
        lambda limit=24: "[Dataset Inventory Context]\n- dataset_id=crash_x | entity_type=crash | origin=user_uploaded",
    )

    client = _client()
    response = client.post("/api/chat", json={"message": "what data do you have", "sessionId": "sess_inventory_ctx"})

    assert response.status_code == 200
    assert "[Dataset Inventory Context]" in runner.last_message_text
    assert "origin=user_uploaded" in runner.last_message_text


def test_build_dispatch_routing_hint_keeps_structured_context_without_memory_hints():
    text = chat._build_dispatch_routing_hint(
        "show traffic",
        last_map={
            "analysis_type": "traffic",
            "label": "Traffic Map",
            "roadAggregateFilter": {"road_name": "I 70"},
            "points": [{"id": 1}],
            "lines": [],
        },
        query_execution_state="[Query Execution State]\n{}",
        analysis_context_state="[Recent Analysis Context]\n{}",
        dataset_inventory_context="[Dataset Inventory Context]\n{}",
    )

    assert "[Query Execution State]" in text
    assert "[Recent Analysis Context]" in text
    assert "[Dataset Inventory Context]" in text
    assert "[Last Map Context]" in text
    assert "[Learned Behavior Hints]" not in text


def test_chat_endpoint_includes_last_map_context_without_learned_hints(monkeypatch):
    runner = _CaptureRunner("ok response")
    monkeypatch.setattr(chat, "runner", runner)
    monkeypatch.setattr(chat, "_build_dataset_inventory_context_block", lambda limit=24: "")
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=12: [])
    monkeypatch.setattr(
        chat,
        "get_last_map",
        lambda _sid: {
            "analysis_type": "traffic",
            "label": "Traffic Map",
            "roadAggregateFilter": {"road_name": "I 70"},
            "points": [{"id": 1}],
            "lines": [],
        },
    )
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: None)
    monkeypatch.setattr(chat, "get_analysis_context", lambda _sid, limit=6: [])

    client = _client()
    response = client.post("/api/chat", json={"message": "show traffic", "sessionId": "sess_last_map_ctx"})

    assert response.status_code == 200
    assert "[Last Map Context]" in runner.last_message_text
    assert '"roadAggregateFilter": {"road_name": "I 70"}' in runner.last_message_text
    assert "[Learned Behavior Hints]" not in runner.last_message_text


def test_chat_endpoint_returns_agent_run_metadata(monkeypatch):
    monkeypatch.setattr(
        chat,
        "runner",
        _UsageRunner(
            "ok response",
            prompt_tokens=120,
            completion_tokens=45,
            total_tokens=165,
            model_version="gemini-2.5-flash",
        ),
    )
    monkeypatch.setattr(chat, "_build_dataset_inventory_context_block", lambda limit=24: "")
    monkeypatch.setattr(chat, "get_execution_events", lambda session_id, limit=80: [])
    monkeypatch.setattr(chat, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "set_active_session", lambda _sid: None)
    monkeypatch.setattr(chat, "get_and_clear_map", lambda _sid: None)
    monkeypatch.setattr(chat, "get_analysis_context", lambda _sid, limit=6: [])

    client = _client()
    response = client.post("/api/chat", json={"message": "hello", "sessionId": "sess_usage"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["agentRunMetadata"]["usage"]["prompt_tokens"] == 120
    assert payload["agentRunMetadata"]["usage"]["completion_tokens"] == 45
    assert payload["agentRunMetadata"]["usage"]["total_tokens"] == 165
    assert payload["agentRunMetadata"]["roles_used"][0]["role"] == "dispatcher"
    assert payload["agentRunMetadata"]["roles_used"][0]["configured_model"] == "gemini-2.5-flash"


def test_chat_clear_endpoint_returns_cleared(monkeypatch):
    monkeypatch.setattr(chat, "runner", _FakeRunner("unused"))
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_analysis_context_for_session", lambda _sid: None)
    monkeypatch.setattr(chat.postgis_store, "clear_session_data", lambda sid: {"sid": sid, "rows": 3})

    client = _client()
    response = client.post("/api/chat/clear", json={"sessionId": "sess_clear", "clearData": True})
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "cleared"
    assert payload["sessionId"] == "sess_clear"
    assert payload["dataCleared"] is True
    assert payload["cleared"] == {"sid": "sess_clear", "rows": 3}


def test_chat_history_and_feedback_endpoints_removed():
    client = _client()
    assert client.get("/api/chat/history").status_code == 404
    assert client.get("/api/chat/history/sess_2").status_code == 404
    response = client.post(
        "/api/chat/feedback",
        json={"sessionId": "sess_3", "messageId": 21, "rating": 5, "note": "unused"},
    )
    assert response.status_code == 404


from unittest.mock import patch, MagicMock
from backend.routers import uploads


def _upload_client() -> TestClient:
    app = FastAPI()
    app.include_router(uploads.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test-user"}
    app.dependency_overrides[uploads.get_current_user] = lambda: {"user_id": "test-user"}
    return TestClient(app, raise_server_exceptions=True)


def test_upload_clears_schema_cache(monkeypatch):
    """RELY-02: Upload endpoint invalidates schema cache for the session."""
    cache_cleared_with = []

    async def _fake_upload(**kwargs):
        return {"status": "ok", "table_name": "test_table"}

    monkeypatch.setattr(uploads, "upload_dataset", _fake_upload)
    monkeypatch.setattr(
        uploads,
        "clear_schema_cache_for_session",
        lambda sid: cache_cleared_with.append(sid),
    )

    client = _upload_client()
    resp = client.post(
        "/api/upload",
        files={"file": ("test.csv", b"a,b\n1,2", "text/csv")},
        headers={"X-Session-Id": "sess-42"},
    )
    assert resp.status_code == 200
    assert "sess-42" in cache_cleared_with

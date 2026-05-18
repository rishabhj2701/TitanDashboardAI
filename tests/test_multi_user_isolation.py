from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.routers import chat, cv_runs, dataset_details, session_datasets
from backend.services import dataset_details as dataset_details_service
from dynamic_analyst.session_state import (
    DEFAULT_DEBUG_USER_ID,
    get_active_user,
    set_active_session,
    set_active_user,
)


def _app_with(*routers) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user_middleware(request: Request, call_next):
        sid = request.headers.get("x-session-id")
        if sid:
            set_active_session(sid)
        debug_user = request.headers.get("x-debug-user-id")
        set_active_user((debug_user or DEFAULT_DEBUG_USER_ID or "").strip())
        return await call_next(request)

    for router in routers:
        app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": get_active_user()}
    return app


class _FakeSessionService:
    async def create_session(self, **_kwargs):
        return None

    async def delete_session(self, **_kwargs):
        return None


class _FakeRunner:
    def __init__(self):
        self.session_service = _FakeSessionService()


class _FakeCVRunCursor:
    def __init__(self, db: "_FakeCVRunDB"):
        self.db = db
        self._one = None
        self._all = []
        self.rowcount = 0

    def execute(self, query, params=None):
        q = " ".join(str(query).split()).lower()
        params = params or ()
        self._one = None
        self._all = []
        self.rowcount = 0

        if q.startswith("set local "):
            return
        if "create table if not exists" in q and "user_cv_run_config" in q:
            return
        if "create index if not exists user_cv_run_config_active_idx" in q and "user_cv_run_config" in q:
            return
        if "select to_regclass('public.cv_runs') as rel" in q:
            self._one = {"rel": "cv_runs"}
            return
        if "select to_regclass(%s) as rel" in q:
            reg = str(params[0]).lower() if params else ""
            self._one = {"rel": "user_cv_run_config"} if reg.endswith("user_cv_run_config") else {"rel": None}
            return
        if "select to_regclass('public.cv_run_config') as rel" in q:
            self._one = {"rel": "cv_run_config"}
            return
        if "select active_run_id from" in q and "user_cv_run_config" in q and "where user_id = %s" in q:
            uid = str(params[0])
            rid = self.db.user_active.get(uid)
            self._one = {"active_run_id": rid} if rid else None
            return
        if "select active_run_id from public.cv_run_config where id = 1" in q:
            self._one = {"active_run_id": self.db.global_active}
            return
        if "from public.cv_runs where run_id = %s" in q:
            rid = str(params[0])
            run = self.db.runs.get(rid)
            self._one = dict(run) if run else None
            return
        if "update" in q and "user_cv_run_config set active_run_id = %s where user_id = %s" in q:
            rid = str(params[0])
            uid = str(params[1])
            if uid in self.db.user_active:
                self.db.user_active[uid] = rid
                self.rowcount = 1
            return
        if "insert into" in q and "user_cv_run_config(user_id, active_run_id)" in q:
            uid = str(params[0])
            rid = str(params[1])
            self.db.user_active[uid] = rid
            self.rowcount = 1
            return
        if "from public.cv_runs" in q and "order by created_at desc, run_id desc" in q:
            rows = sorted(
                self.db.runs.values(),
                key=lambda r: (str(r.get("created_at") or ""), str(r.get("run_id") or "")),
                reverse=True,
            )
            self._all = [dict(r) for r in rows]
            return

        raise AssertionError(f"Unhandled SQL in test fake cursor: {query}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCVRunConn:
    def __init__(self, db: "_FakeCVRunDB"):
        self.db = db

    def cursor(self, cursor_factory=None):
        return _FakeCVRunCursor(self.db)

    def get_dsn_parameters(self):
        return {"dbname": "traffic"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCVRunDB:
    def __init__(self):
        self.runs = {
            "run_global": {
                "run_id": "run_global",
                "schema_name": "cv_global",
                "created_at": "2026-02-01T00:00:00Z",
                "display_name": "Global Run",
                "season_tag": "Winter",
                "state_code": "MO",
                "point_count": 100,
                "ts_start": "2026-02-01T00:00:00Z",
                "ts_end": "2026-02-02T00:00:00Z",
                "is_visible": True,
            },
            "run_a": {
                "run_id": "run_a",
                "schema_name": "cv_a",
                "created_at": "2026-02-10T00:00:00Z",
                "display_name": "Run A",
                "season_tag": "Winter",
                "state_code": "MO",
                "point_count": 200,
                "ts_start": "2026-02-10T00:00:00Z",
                "ts_end": "2026-02-11T00:00:00Z",
                "is_visible": True,
            },
            "run_b": {
                "run_id": "run_b",
                "schema_name": "cv_b",
                "created_at": "2026-02-20T00:00:00Z",
                "display_name": "Run B",
                "season_tag": "Winter",
                "state_code": "MO",
                "point_count": 300,
                "ts_start": "2026-02-20T00:00:00Z",
                "ts_end": "2026-02-21T00:00:00Z",
                "is_visible": True,
            },
        }
        self.global_active = "run_global"
        self.user_active: dict[str, str] = {}

    def connect(self):
        return _FakeCVRunConn(self)


def test_datasets_shared_across_sessions_for_same_user_and_isolated_by_user(monkeypatch):
    datasets_by_user = {
        "alice": [{"dataset_id": "crash_alice", "name": "alice crashes"}],
        "bob": [{"dataset_id": "crash_bob", "name": "bob crashes"}],
        DEFAULT_DEBUG_USER_ID: [{"dataset_id": "crash_default", "name": "default crashes"}],
    }

    monkeypatch.setattr(
        session_datasets,
        "list_session_datasets",
        lambda: datasets_by_user.get(get_active_user(), []),
    )

    client = TestClient(_app_with(session_datasets.router))

    r1 = client.get("/api/datasets", headers={"X-Session-Id": "s1", "X-Debug-User-Id": "alice"})
    r2 = client.get("/api/datasets", headers={"X-Session-Id": "s2", "X-Debug-User-Id": "alice"})
    r3 = client.get("/api/datasets", headers={"X-Session-Id": "s3", "X-Debug-User-Id": "bob"})

    assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
    assert r1.json()["datasets"] == r2.json()["datasets"]
    assert r1.json()["datasets"] != r3.json()["datasets"]
    assert r1.json()["datasets"][0]["dataset_id"] == "crash_alice"
    assert r3.json()["datasets"][0]["dataset_id"] == "crash_bob"


def test_active_cv_run_is_user_specific_with_global_fallback(monkeypatch):
    db = _FakeCVRunDB()
    monkeypatch.setattr(cv_runs.postgis_store, "_conn", db.connect)

    client = TestClient(_app_with(cv_runs.router))

    set_a = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sa", "X-Debug-User-Id": "alice"},
        json={"run_id": "run_a"},
    )
    set_b = client.post(
        "/api/cv/active-run",
        headers={"X-Session-Id": "sb", "X-Debug-User-Id": "bob"},
        json={"run_id": "run_b"},
    )
    assert set_a.status_code == 200
    assert set_b.status_code == 200

    get_a = client.get("/api/cv/active-run", headers={"X-Session-Id": "sa2", "X-Debug-User-Id": "alice"})
    get_b = client.get("/api/cv/active-run", headers={"X-Session-Id": "sb2", "X-Debug-User-Id": "bob"})
    get_c = client.get("/api/cv/active-run", headers={"X-Session-Id": "sc", "X-Debug-User-Id": "charlie"})

    assert get_a.status_code == 200
    assert get_b.status_code == 200
    assert get_c.status_code == 200
    assert get_a.json()["active_run"]["run_id"] == "run_a"
    assert get_b.json()["active_run"]["run_id"] == "run_b"
    assert get_c.json()["active_run"]["run_id"] == "run_global"

    runs_a = client.get("/api/cv/runs", headers={"X-Session-Id": "sa3", "X-Debug-User-Id": "alice"})
    runs_b = client.get("/api/cv/runs", headers={"X-Session-Id": "sb3", "X-Debug-User-Id": "bob"})
    assert runs_a.status_code == 200
    assert runs_b.status_code == 200
    assert runs_a.json()["active_run_id"] == "run_a"
    assert runs_b.json()["active_run_id"] == "run_b"


def test_chat_clear_data_is_session_only_per_user(monkeypatch):
    monkeypatch.setattr(chat, "runner", _FakeRunner())
    monkeypatch.setattr(chat, "clear_maps_for_session", lambda _sid: None)
    monkeypatch.setattr(chat, "clear_execution_for_session", lambda _sid: None)

    state: dict[tuple[str, str], int] = {
        ("alice", "sess1"): 3,
        ("alice", "sess2"): 5,
        ("bob", "sess1"): 7,
    }

    def _clear_session_data(session_id: str):
        uid = get_active_user()
        deleted = state.pop((uid, session_id), 0)
        return {"events_deleted": deleted, "datasets_deleted": 1 if deleted else 0}

    monkeypatch.setattr(chat.postgis_store, "clear_session_data", _clear_session_data)

    client = TestClient(_app_with(chat.router))

    r1 = client.post(
        "/api/chat/clear",
        json={"sessionId": "sess1", "clearData": True},
        headers={"X-Session-Id": "sess1", "X-Debug-User-Id": "alice"},
    )
    assert r1.status_code == 200
    assert r1.json()["cleared"]["events_deleted"] == 3
    assert ("alice", "sess2") in state
    assert ("bob", "sess1") in state

    r2 = client.post(
        "/api/chat/clear",
        json={"sessionId": "sess2", "clearData": True},
        headers={"X-Session-Id": "sess2", "X-Debug-User-Id": "alice"},
    )
    assert r2.status_code == 200
    assert r2.json()["cleared"]["events_deleted"] == 5
    assert ("bob", "sess1") in state


def test_dataset_details_route_denies_cross_user_access(monkeypatch):
    class _FakeStore:
        def __init__(self):
            self._rows = {
                "alice": {
                    "crash_alice_1": {
                        "dataset_id": "crash_alice_1",
                        "name": "Alice Crash",
                        "entity_type": "cv",
                        "status": "ready",
                        "created_at": "2026-02-01T00:00:00Z",
                        "stats": {},
                        "mapping": {},
                    }
                },
                "bob": {
                    "crash_bob_1": {
                        "dataset_id": "crash_bob_1",
                        "name": "Bob Crash",
                        "entity_type": "cv",
                        "status": "ready",
                        "created_at": "2026-02-02T00:00:00Z",
                        "stats": {},
                        "mapping": {},
                    }
                },
            }

        def get_dataset(self, dataset_id: str):
            uid = get_active_user()
            row = self._rows.get(uid, {}).get(dataset_id)
            if not row:
                raise ValueError("Dataset not found for this user")
            return dict(row)

        def list_codebook_attributes(self):
            return []

    fake_store = _FakeStore()
    monkeypatch.setattr(dataset_details_service, "_default_store", lambda: fake_store)

    client = TestClient(_app_with(dataset_details.router))

    ok = client.get(
        "/api/datasets-id/crash_alice_1",
        headers={"X-Session-Id": "sa", "X-Debug-User-Id": "alice"},
    )
    denied = client.get(
        "/api/datasets-id/crash_alice_1",
        headers={"X-Session-Id": "sb", "X-Debug-User-Id": "bob"},
    )
    ok_bob = client.get(
        "/api/datasets-id/crash_bob_1",
        headers={"X-Session-Id": "sb2", "X-Debug-User-Id": "bob"},
    )

    assert ok.status_code == 200
    assert ok.json()["dataset_id"] == "crash_alice_1"
    assert denied.status_code == 404
    assert "not found" in denied.json()["detail"].lower()
    assert ok_bob.status_code == 200
    assert ok_bob.json()["dataset_id"] == "crash_bob_1"

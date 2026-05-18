from dynamic_analyst import session_state


def test_get_scoped_session_key_rebinds_scoped_ids_to_active_user():
    session_state.set_active_user("user_1")

    scoped = session_state.get_scoped_session_key(session_id="other_user::sess_abc")
    assert scoped == "user_1::sess_abc"


def test_get_scoped_session_key_scopes_raw_id_by_user():
    session_state.set_active_user("user_2")

    scoped = session_state.get_scoped_session_key(session_id="sess_xyz")
    assert scoped == "user_2::sess_xyz"


def test_chat_lock_and_inflight_memory_fallback(monkeypatch):
    monkeypatch.setattr(session_state, "_redis", lambda: None)
    key = "user_1::sess_lock"

    token = session_state.acquire_chat_lock(key, ttl_seconds=30)
    assert token
    assert session_state.acquire_chat_lock(key, ttl_seconds=30) is None

    session_state.set_chat_inflight_question(key, "Hello world", ttl_seconds=30)
    assert session_state.get_chat_inflight_question(key) == "hello world"

    session_state.clear_chat_inflight_question(key)
    assert session_state.get_chat_inflight_question(key) == ""

    session_state.release_chat_lock(key, token)
    assert session_state.acquire_chat_lock(key, ttl_seconds=30)


def test_analysis_context_roundtrip_memory_fallback(monkeypatch):
    monkeypatch.setattr(session_state, "_redis", lambda: None)
    session_state.set_active_user("analysis_user")
    session_state.set_active_session("sess_analysis")
    session_state.clear_analysis_context_for_session("sess_analysis")

    session_state.append_analysis_context(
        {
            "analysis_type": "area",
            "response_summary": "Area analysis complete",
            "important_metrics": {"points": 42},
        },
        session_id="sess_analysis",
    )

    rows = session_state.get_analysis_context("sess_analysis", limit=5)
    assert len(rows) == 1
    assert rows[0]["analysis_type"] == "area"
    assert rows[0]["important_metrics"]["points"] == 42
    assert rows[0].get("created_at")

    session_state.clear_analysis_context_for_session("sess_analysis")
    assert session_state.get_analysis_context("sess_analysis", limit=5) == []


def test_analysis_context_honors_max_entries(monkeypatch):
    monkeypatch.setattr(session_state, "_redis", lambda: None)
    monkeypatch.setattr(session_state, "MAX_ANALYSIS_CONTEXT_EVENTS", 3)
    session_state.set_active_user("analysis_user_cap")
    session_state.set_active_session("sess_cap")
    session_state.clear_analysis_context_for_session("sess_cap")

    for idx in range(5):
        session_state.append_analysis_context(
            {
                "analysis_type": "crash",
                "response_summary": f"crash {idx}",
                "important_metrics": {"idx": idx},
            },
            session_id="sess_cap",
        )

    rows = session_state.get_analysis_context("sess_cap", limit=10)
    assert len(rows) == 3
    assert [row["important_metrics"]["idx"] for row in rows] == [2, 3, 4]

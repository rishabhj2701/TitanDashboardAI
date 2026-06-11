import asyncio
import time

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events.event import Event

from dynamic_analyst.adk_session_service import RedisSessionService


class _FakeRedis:
    def __init__(self):
        self._kv = {}
        self._sets = {}
        self._zsets = {}
        self._ttl = {}
        self._set_kwargs = {}

    def ping(self):
        return True

    def get(self, key):
        return self._kv.get(key)

    def set(self, key, value, **_kwargs):
        self._kv[key] = value
        self._set_kwargs[key] = dict(_kwargs)
        return True

    def delete(self, key):
        self._kv.pop(key, None)
        self._sets.pop(key, None)
        self._zsets.pop(key, None)
        self._ttl.pop(key, None)
        return 1

    def sadd(self, key, *values):
        bucket = self._sets.setdefault(key, set())
        for value in values:
            bucket.add(value)
        return len(values)

    def srem(self, key, *values):
        bucket = self._sets.setdefault(key, set())
        for value in values:
            bucket.discard(value)
        return len(values)

    def smembers(self, key):
        return set(self._sets.get(key, set()))

    def scard(self, key):
        return len(self._sets.get(key, set()))

    def zadd(self, key, mapping):
        bucket = self._zsets.setdefault(key, {})
        for member, score in (mapping or {}).items():
            bucket[str(member)] = float(score)
        return len(mapping or {})

    def zrem(self, key, *members):
        bucket = self._zsets.setdefault(key, {})
        removed = 0
        for member in members:
            if str(member) in bucket:
                removed += 1
                bucket.pop(str(member), None)
        return removed

    def zrange(self, key, start, stop):
        bucket = self._zsets.get(key, {})
        ordered = [m for m, _s in sorted(bucket.items(), key=lambda item: (item[1], item[0]))]
        if not ordered:
            return []
        n = len(ordered)
        if start < 0:
            start = max(0, n + start)
        if stop < 0:
            stop = n + stop
        if stop >= n:
            stop = n - 1
        if start > stop or start >= n:
            return []
        return ordered[start : stop + 1]

    def zcard(self, key):
        return len(self._zsets.get(key, {}))

    def expire(self, key, ttl_seconds):
        self._ttl[key] = int(ttl_seconds)
        return 1

    def ttl(self, key):
        return int(self._ttl.get(key, -1))

    def exists(self, key):
        return int(key in self._kv or key in self._sets or key in self._zsets)


def test_service_falls_back_to_in_memory_when_redis_missing(monkeypatch):
    monkeypatch.setattr("dynamic_analyst.adk_session_service._build_redis_client", lambda _url: None)
    service = RedisSessionService(redis_url="redis://fake")
    assert service.using_redis is False

    session = asyncio.run(
        service.create_session(app_name="app", user_id="u1", session_id="s1", state={"a": 1})
    )
    loaded = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert session.id == "s1"
    assert loaded is not None
    assert loaded.state.get("a") == 1


def test_redis_service_shares_session_across_instances():
    store = _FakeRedis()
    service_a = RedisSessionService(redis_client=store, key_prefix="t")
    service_b = RedisSessionService(redis_client=store, key_prefix="t")

    created = asyncio.run(
        service_a.create_session(
            app_name="app",
            user_id="u1",
            session_id="s1",
            state={"app:global": "A", "user:pref": "B", "local": 1},
        )
    )
    assert created.state["app:global"] == "A"
    assert created.state["user:pref"] == "B"
    assert created.state["local"] == 1

    loaded = asyncio.run(service_b.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert loaded is not None
    assert loaded.state["app:global"] == "A"
    assert loaded.state["user:pref"] == "B"
    assert loaded.state["local"] == 1

    with_raises = False
    try:
        asyncio.run(service_b.create_session(app_name="app", user_id="u1", session_id="s1"))
    except AlreadyExistsError:
        with_raises = True
    assert with_raises is True


def test_append_event_updates_session_and_scoped_state():
    store = _FakeRedis()
    service_a = RedisSessionService(redis_client=store, key_prefix="t")
    service_b = RedisSessionService(redis_client=store, key_prefix="t")

    asyncio.run(service_a.create_session(app_name="app", user_id="u1", session_id="s1"))
    session = asyncio.run(service_b.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert session is not None

    event = Event(author="agent", invocation_id="inv-1")
    event.actions.state_delta = {
        "app:new_global": "G",
        "user:new_pref": "U",
        "local_value": 42,
        "temp:transient": "ignore",
    }

    asyncio.run(service_b.append_event(session, event))

    loaded = asyncio.run(service_a.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert loaded is not None
    assert len(loaded.events) == 1
    assert loaded.state.get("app:new_global") == "G"
    assert loaded.state.get("user:new_pref") == "U"
    assert loaded.state.get("local_value") == 42
    assert "temp:transient" not in loaded.state

    listed = asyncio.run(service_a.list_sessions(app_name="app", user_id="u1"))
    assert len(listed.sessions) == 1
    assert listed.sessions[0].events == []


def test_session_key_has_expiry_and_tracking_sets_are_bounded():
    store = _FakeRedis()
    service = RedisSessionService(
        redis_client=store,
        key_prefix="t",
        session_ttl_seconds=600,
    )
    created = asyncio.run(service.create_session(app_name="app", user_id="u1", session_id="s1"))
    session_key = service._k_session("app", "u1", created.id)
    assert store._set_kwargs[session_key]["ex"] == 600
    assert store.ttl(service._k_user_sessions("app", "u1")) == 600
    assert store.ttl(service._k_user_session_index("app", "u1")) == 600
    assert store.ttl(service._k_app_users("app")) == 600


def test_append_event_trims_session_history_to_max_events():
    store = _FakeRedis()
    service = RedisSessionService(
        redis_client=store,
        key_prefix="t",
        session_max_events=3,
    )
    asyncio.run(service.create_session(app_name="app", user_id="u1", session_id="s1"))
    session = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert session is not None

    for idx in range(5):
        event = Event(author="agent", invocation_id=f"inv-{idx}")
        asyncio.run(service.append_event(session, event))

    loaded = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s1"))
    assert loaded is not None
    assert len(loaded.events) == 3
    assert [evt.invocation_id for evt in loaded.events] == ["inv-2", "inv-3", "inv-4"]


def test_create_session_rotates_oldest_sessions_per_user():
    store = _FakeRedis()
    service = RedisSessionService(
        redis_client=store,
        key_prefix="t",
        max_sessions_per_user=2,
    )
    asyncio.run(service.create_session(app_name="app", user_id="u1", session_id="s1"))
    time.sleep(0.01)
    asyncio.run(service.create_session(app_name="app", user_id="u1", session_id="s2"))
    time.sleep(0.01)
    asyncio.run(service.create_session(app_name="app", user_id="u1", session_id="s3"))

    loaded_s1 = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s1"))
    loaded_s2 = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s2"))
    loaded_s3 = asyncio.run(service.get_session(app_name="app", user_id="u1", session_id="s3"))
    assert loaded_s1 is None
    assert loaded_s2 is not None
    assert loaded_s3 is not None

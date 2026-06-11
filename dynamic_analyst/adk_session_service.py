from __future__ import annotations

import copy
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events.event import Event
from google.adk.sessions import InMemorySessionService
from google.adk.sessions import _session_util
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.adk.sessions.session import Session
from google.adk.sessions.state import State

try:
    from redis import Redis
except Exception:  # pragma: no cover - optional dependency fallback
    Redis = None  # type: ignore[assignment]

logger = logging.getLogger("adk_server")

REDIS_URL = os.environ.get("REDIS_URL", "").strip()
ADK_SESSION_PREFIX = os.environ.get("ADK_SESSION_PREFIX", "ta:adk").strip() or "ta:adk"
ADK_SESSION_TTL_SECONDS = max(300, int(os.environ.get("ADK_SESSION_TTL_SECONDS", "1209600")))
ADK_SESSION_MAX_EVENTS = max(1, int(os.environ.get("ADK_SESSION_MAX_EVENTS", "300")))
ADK_MAX_SESSIONS_PER_USER = max(1, int(os.environ.get("ADK_MAX_SESSIONS_PER_USER", "30")))


def _build_redis_client(redis_url: str) -> Optional["Redis"]:
    if not redis_url or Redis is None:
        return None
    try:
        client = Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("adk.session.redis.unavailable fallback=in_memory error=%s", exc)
        return None


class RedisSessionService(BaseSessionService):
    """ADK SessionService backed by Redis with in-memory fallback.

    Keeps ADK session state shared across workers/processes while preserving
    InMemorySessionService semantics for app/user/session state partitioning.
    """

    def __init__(
        self,
        *,
        redis_client: Optional["Redis"] = None,
        redis_url: Optional[str] = None,
        key_prefix: Optional[str] = None,
        fallback_service: Optional[InMemorySessionService] = None,
        session_ttl_seconds: Optional[int] = None,
        session_max_events: Optional[int] = None,
        max_sessions_per_user: Optional[int] = None,
    ):
        self._redis = redis_client or _build_redis_client(redis_url or REDIS_URL)
        self._fallback = fallback_service or InMemorySessionService()
        self._prefix = (key_prefix or ADK_SESSION_PREFIX).strip() or ADK_SESSION_PREFIX
        self._session_ttl_seconds = max(300, int(session_ttl_seconds or ADK_SESSION_TTL_SECONDS))
        self._session_max_events = max(1, int(session_max_events or ADK_SESSION_MAX_EVENTS))
        self._max_sessions_per_user = max(1, int(max_sessions_per_user or ADK_MAX_SESSIONS_PER_USER))

    @property
    def using_redis(self) -> bool:
        return self._redis is not None

    def _k_session(self, app_name: str, user_id: str, session_id: str) -> str:
        return f"{self._prefix}:session:{app_name}:{user_id}:{session_id}"

    def _k_user_sessions(self, app_name: str, user_id: str) -> str:
        return f"{self._prefix}:user_sessions:{app_name}:{user_id}"

    def _k_app_users(self, app_name: str) -> str:
        return f"{self._prefix}:app_users:{app_name}"

    def _k_user_session_index(self, app_name: str, user_id: str) -> str:
        return f"{self._prefix}:user_session_idx:{app_name}:{user_id}"

    def _k_app_state(self, app_name: str) -> str:
        return f"{self._prefix}:app_state:{app_name}"

    def _k_user_state(self, app_name: str, user_id: str) -> str:
        return f"{self._prefix}:user_state:{app_name}:{user_id}"

    def _dump(self, data: Any) -> str:
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    def _now(self) -> float:
        return time.time()

    def _touch_tracking_sets(self, app_name: str, user_id: str) -> None:
        if self._redis is None:
            return
        ttl = self._session_ttl_seconds
        self._redis.expire(self._k_user_sessions(app_name, user_id), ttl)
        self._redis.expire(self._k_user_session_index(app_name, user_id), ttl)
        self._redis.expire(self._k_app_users(app_name), ttl)

    def _remove_session_refs(self, app_name: str, user_id: str, session_id: str) -> None:
        if self._redis is None:
            return
        self._redis.delete(self._k_session(app_name, user_id, session_id))
        self._redis.srem(self._k_user_sessions(app_name, user_id), session_id)
        self._redis.zrem(self._k_user_session_index(app_name, user_id), session_id)

    def _cleanup_empty_user_tracking(self, app_name: str, user_id: str) -> None:
        if self._redis is None:
            return
        user_sessions_key = self._k_user_sessions(app_name, user_id)
        if self._redis.scard(user_sessions_key) > 0:
            return
        self._redis.delete(user_sessions_key)
        self._redis.delete(self._k_user_session_index(app_name, user_id))
        self._redis.srem(self._k_app_users(app_name), user_id)
        if self._redis.scard(self._k_app_users(app_name)) <= 0:
            self._redis.delete(self._k_app_users(app_name))

    def _prune_stale_user_session_refs(self, app_name: str, user_id: str) -> None:
        if self._redis is None:
            return
        sessions_key = self._k_user_sessions(app_name, user_id)
        index_key = self._k_user_session_index(app_name, user_id)
        known_ids = set(self._redis.smembers(sessions_key))
        known_ids.update(self._redis.zrange(index_key, 0, -1))
        if not known_ids:
            self._cleanup_empty_user_tracking(app_name, user_id)
            return
        stale: list[str] = []
        for sid in known_ids:
            if not self._redis.exists(self._k_session(app_name, user_id, sid)):
                stale.append(sid)
        if stale:
            self._redis.srem(sessions_key, *stale)
            self._redis.zrem(index_key, *stale)
        self._cleanup_empty_user_tracking(app_name, user_id)

    def _enforce_user_session_limit(
        self,
        app_name: str,
        user_id: str,
        *,
        keep_session_id: Optional[str] = None,
    ) -> None:
        if self._redis is None:
            return
        self._prune_stale_user_session_refs(app_name, user_id)
        members = self._redis.zrange(self._k_user_session_index(app_name, user_id), 0, -1)
        if len(members) <= self._max_sessions_per_user:
            return
        excess = len(members) - self._max_sessions_per_user
        victims = [sid for sid in members if sid != keep_session_id][:excess]
        for sid in victims:
            self._remove_session_refs(app_name, user_id, sid)
        if victims:
            logger.info(
                "adk.session.rotate app=%s user=%s removed=%s limit=%s",
                app_name,
                user_id,
                len(victims),
                self._max_sessions_per_user,
            )
        self._cleanup_empty_user_tracking(app_name, user_id)

    def _load_session(self, app_name: str, user_id: str, session_id: str) -> Optional[Session]:
        if self._redis is None:
            return None
        raw = self._redis.get(self._k_session(app_name, user_id, session_id))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return Session.model_validate(payload)
        except Exception as exc:
            logger.warning(
                "adk.session.decode_error app=%s user=%s session=%s error=%s",
                app_name,
                user_id,
                session_id,
                exc,
            )
            return None

    def _save_session(self, session: Session) -> None:
        if self._redis is None:
            return
        session_key = self._k_session(session.app_name, session.user_id, session.id)
        self._redis.set(
            session_key,
            self._dump(session.model_dump(mode="json", by_alias=False)),
            ex=self._session_ttl_seconds,
        )
        self._redis.sadd(self._k_user_sessions(session.app_name, session.user_id), session.id)
        self._redis.zadd(
            self._k_user_session_index(session.app_name, session.user_id),
            {session.id: float(session.last_update_time or self._now())},
        )
        self._redis.sadd(self._k_app_users(session.app_name), session.user_id)
        self._touch_tracking_sets(session.app_name, session.user_id)

    def _load_app_state(self, app_name: str) -> dict[str, Any]:
        if self._redis is None:
            return {}
        raw = self._redis.get(self._k_app_state(app_name))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_app_state(self, app_name: str, data: dict[str, Any]) -> None:
        if self._redis is None:
            return
        self._redis.set(self._k_app_state(app_name), self._dump(data))

    def _load_user_state(self, app_name: str, user_id: str) -> dict[str, Any]:
        if self._redis is None:
            return {}
        raw = self._redis.get(self._k_user_state(app_name, user_id))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_user_state(self, app_name: str, user_id: str, data: dict[str, Any]) -> None:
        if self._redis is None:
            return
        self._redis.set(self._k_user_state(app_name, user_id), self._dump(data))

    def _merge_state(self, app_name: str, user_id: str, copied_session: Session) -> Session:
        app_state = self._load_app_state(app_name)
        for key, value in app_state.items():
            copied_session.state[State.APP_PREFIX + key] = value

        user_state = self._load_user_state(app_name, user_id)
        for key, value in user_state.items():
            copied_session.state[State.USER_PREFIX + key] = value
        return copied_session

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        if self._redis is None:
            return await self._fallback.create_session(
                app_name=app_name,
                user_id=user_id,
                state=state,
                session_id=session_id,
            )

        sid = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
        if self._load_session(app_name, user_id, sid) is not None:
            raise AlreadyExistsError(f"Session with id {sid} already exists.")

        state_deltas = _session_util.extract_state_delta(state or {})
        app_state_delta = state_deltas["app"]
        user_state_delta = state_deltas["user"]
        session_state_delta = state_deltas["session"]

        if app_state_delta:
            app_state = self._load_app_state(app_name)
            app_state.update(app_state_delta)
            self._save_app_state(app_name, app_state)

        if user_state_delta:
            user_state = self._load_user_state(app_name, user_id)
            user_state.update(user_state_delta)
            self._save_user_state(app_name, user_id, user_state)

        storage_session = Session(
            app_name=app_name,
            user_id=user_id,
            id=sid,
            state=session_state_delta or {},
            last_update_time=self._now(),
        )
        self._save_session(storage_session)
        self._enforce_user_session_limit(app_name, user_id, keep_session_id=sid)

        copied_session = copy.deepcopy(storage_session)
        return self._merge_state(app_name, user_id, copied_session)

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        if self._redis is None:
            return await self._fallback.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                config=config,
            )

        storage_session = self._load_session(app_name, user_id, session_id)
        if storage_session is None:
            return None
        self._redis.expire(self._k_session(app_name, user_id, session_id), self._session_ttl_seconds)
        self._touch_tracking_sets(app_name, user_id)

        copied_session = copy.deepcopy(storage_session)
        if config:
            if config.num_recent_events:
                copied_session.events = copied_session.events[-config.num_recent_events :]
            if config.after_timestamp:
                i = len(copied_session.events) - 1
                while i >= 0:
                    if copied_session.events[i].timestamp < config.after_timestamp:
                        break
                    i -= 1
                if i >= 0:
                    copied_session.events = copied_session.events[i + 1 :]

        return self._merge_state(app_name, user_id, copied_session)

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        if self._redis is None:
            return await self._fallback.list_sessions(app_name=app_name, user_id=user_id)

        sessions_without_events: list[Session] = []
        if user_id is None:
            user_ids = self._redis.smembers(self._k_app_users(app_name))
        else:
            user_ids = {user_id}

        for uid in user_ids:
            self._prune_stale_user_session_refs(app_name, uid)
            session_ids = self._redis.smembers(self._k_user_sessions(app_name, uid))
            for sid in session_ids:
                storage_session = self._load_session(app_name, uid, sid)
                if storage_session is None:
                    continue
                copied_session = copy.deepcopy(storage_session)
                copied_session.events = []
                copied_session = self._merge_state(app_name, uid, copied_session)
                sessions_without_events.append(copied_session)

        return ListSessionsResponse(sessions=sessions_without_events)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        if self._redis is None:
            await self._fallback.delete_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
            return

        self._remove_session_refs(app_name, user_id, session_id)
        self._cleanup_empty_user_tracking(app_name, user_id)

    async def append_event(self, session: Session, event: Event) -> Event:
        if self._redis is None:
            return await self._fallback.append_event(session=session, event=event)
        if event.partial:
            return event

        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        storage_session = self._load_session(app_name, user_id, session_id)
        if storage_session is None:
            logger.warning(
                "adk.session.append.missing app=%s user=%s session=%s",
                app_name,
                user_id,
                session_id,
            )
            return event

        await super().append_event(session=session, event=event)
        session.last_update_time = event.timestamp

        storage_session.events.append(event)
        storage_session.last_update_time = event.timestamp
        if len(storage_session.events) > self._session_max_events:
            storage_session.events = storage_session.events[-self._session_max_events :]

        if event.actions and event.actions.state_delta:
            state_deltas = _session_util.extract_state_delta(event.actions.state_delta)
            app_state_delta = state_deltas["app"]
            user_state_delta = state_deltas["user"]
            session_state_delta = state_deltas["session"]

            if app_state_delta:
                app_state = self._load_app_state(app_name)
                app_state.update(app_state_delta)
                self._save_app_state(app_name, app_state)

            if user_state_delta:
                user_state = self._load_user_state(app_name, user_id)
                user_state.update(user_state_delta)
                self._save_user_state(app_name, user_id, user_state)

            if session_state_delta:
                storage_session.state.update(session_state_delta)

        self._save_session(storage_session)
        self._enforce_user_session_limit(app_name, user_id, keep_session_id=session_id)
        return event


def create_chat_session_service() -> BaseSessionService:
    """Builds the ADK session service used by the chat runner."""
    service = RedisSessionService()
    if service.using_redis:
        logger.info(
            "adk.session.service=redis prefix=%s ttl_s=%s max_events=%s max_sessions_per_user=%s",
            ADK_SESSION_PREFIX,
            ADK_SESSION_TTL_SECONDS,
            ADK_SESSION_MAX_EVENTS,
            ADK_MAX_SESSIONS_PER_USER,
        )
    else:
        logger.info("adk.session.service=in_memory")
    return service

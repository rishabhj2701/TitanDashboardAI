import contextvars
import logging
import os
import json
import secrets
from datetime import date, datetime, timezone
from typing import Dict, Any, Optional, List

try:
    from redis import Redis
except Exception:  # pragma: no cover - optional dependency fallback
    Redis = None  # type: ignore[assignment]

logger = logging.getLogger("adk_server")

# 1. The Context Variable
current_session_id = contextvars.ContextVar("session_id", default=None)
current_user_id = contextvars.ContextVar("user_id", default=None)

# 2. The Map Store - now supports multiple maps per session keyed by map_type
# Structure: {session_id: {map_type: map_data}}
MAP_STORAGE: Dict[str, Dict[str, Any]] = {}

# 3. Global Fallback (The Safety Net for lost threads)
# Structure: {map_type: map_data}
LAST_GENERATED_MAP: Dict[str, Any] = {}
LAST_RETURNED_MAP: Dict[str, Any] = {}
EXECUTION_STORAGE: Dict[str, List[Dict[str, Any]]] = {}
ANALYSIS_CONTEXT_STORAGE: Dict[str, List[Dict[str, Any]]] = {}
MAX_EXECUTION_EVENTS = 80
MAX_ANALYSIS_CONTEXT_EVENTS = max(1, int(os.environ.get("MAX_ANALYSIS_CONTEXT_EVENTS", "12")))
DEFAULT_DEBUG_USER_ID = os.environ.get("DEFAULT_DEBUG_USER_ID", "dev-user").strip() or "dev-user"
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
STATE_REDIS_PREFIX = os.environ.get("STATE_REDIS_PREFIX", "ta").strip() or "ta"
STATE_MAP_TTL_SECONDS = max(60, int(os.environ.get("STATE_MAP_TTL_SECONDS", "1800")))
STATE_EXEC_TTL_SECONDS = max(60, int(os.environ.get("STATE_EXEC_TTL_SECONDS", "1800")))
STATE_ANALYSIS_TTL_SECONDS = max(60, int(os.environ.get("STATE_ANALYSIS_TTL_SECONDS", "86400")))
STATE_CHAT_TTL_SECONDS = max(15, int(os.environ.get("STATE_CHAT_TTL_SECONDS", "90")))
_REDIS_CLIENT: Optional["Redis"] = None
_REDIS_UNAVAILABLE = False
_CHAT_LOCKS: Dict[str, str] = {}
_CHAT_INFLIGHT: Dict[str, str] = {}


def _redis() -> Optional["Redis"]:
    global _REDIS_CLIENT, _REDIS_UNAVAILABLE
    if _REDIS_UNAVAILABLE:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if not REDIS_URL or Redis is None:
        return None
    try:
        client = Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        logger.info("state.redis.connected url=%s", REDIS_URL)
        return _REDIS_CLIENT
    except Exception as exc:
        _REDIS_UNAVAILABLE = True
        logger.warning("state.redis.unavailable fallback=in_memory error=%s", exc)
        return None


def _rkey(kind: str, scoped: str) -> str:
    return f"{STATE_REDIS_PREFIX}:{kind}:{scoped}"


def set_active_user(user_id: Optional[str]):
    current_user_id.set((user_id or "").strip() or DEFAULT_DEBUG_USER_ID)


def get_active_user() -> str:
    return (current_user_id.get() or "").strip() or DEFAULT_DEBUG_USER_ID


def get_scoped_session_key(session_id: Optional[str] = None, user_id: Optional[str] = None) -> Optional[str]:
    raw_sid = (session_id or get_active_session() or "").strip()
    uid = (user_id or get_active_user() or "").strip()
    sid = raw_sid
    # Prevent caller-supplied spoofed scoped keys from crossing user boundaries.
    # If a scoped token arrives, strip the prefix and re-scope to active user.
    if sid and "::" in sid:
        sid = sid.split("::", 1)[1].strip()
    if sid and uid:
        return f"{uid}::{sid}"
    if sid:
        return sid
    if uid:
        return f"{uid}::__nosession__"
    return None

def set_active_session(session_id: str):
    """Call this at the start of a request in server.py"""
    current_session_id.set(session_id)

def get_active_session() -> str:
    """Call this inside the tool to know who is asking"""
    return current_session_id.get()

def clear_maps_for_session(session_id: Optional[str] = None):
    """Clear all stored maps for a session (call at start of new request)."""
    global LAST_GENERATED_MAP
    scoped = get_scoped_session_key(session_id=session_id)
    client = _redis()
    if client and scoped:
        client.delete(_rkey("map:types", scoped))
    if scoped and scoped in MAP_STORAGE:
        MAP_STORAGE[scoped] = {}
    LAST_GENERATED_MAP = {}


def clear_execution_for_session(session_id: Optional[str] = None):
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped:
        return
    client = _redis()
    if client:
        client.delete(_rkey("exec", scoped))
    EXECUTION_STORAGE.pop(scoped, None)


def clear_analysis_context_for_session(session_id: Optional[str] = None):
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped:
        return
    client = _redis()
    if client:
        client.delete(_rkey("analysis", scoped))
    ANALYSIS_CONTEXT_STORAGE.pop(scoped, None)


def _json_safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(getattr(value, "isoformat", None)):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item") and callable(getattr(value, "item", None)):
        try:
            return value.item()
        except Exception:
            pass
    return value


def sanitize_map_payload(map_data: Any) -> Any:
    """Make map payloads JSON-serializable for Redis and FastAPI responses."""
    if isinstance(map_data, dict):
        return {str(k): sanitize_map_payload(v) for k, v in map_data.items()}
    if isinstance(map_data, list):
        return [sanitize_map_payload(v) for v in map_data]
    return _json_safe_scalar(map_data)


def append_execution_event(event: Dict[str, Any], session_id: Optional[str] = None):
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped or not isinstance(event, dict):
        return
    row = dict(event)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    client = _redis()
    if client:
        key = _rkey("exec", scoped)
        client.rpush(
            key,
            json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str),
        )
        client.ltrim(key, -MAX_EXECUTION_EVENTS, -1)
        client.expire(key, STATE_EXEC_TTL_SECONDS)
        return
    entries = EXECUTION_STORAGE.setdefault(scoped, [])
    entries.append(row)
    if len(entries) > MAX_EXECUTION_EVENTS:
        EXECUTION_STORAGE[scoped] = entries[-MAX_EXECUTION_EVENTS:]


def append_analysis_context(event: Dict[str, Any], session_id: Optional[str] = None):
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped or not isinstance(event, dict):
        return
    row = dict(event)
    row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    client = _redis()
    if client:
        key = _rkey("analysis", scoped)
        client.rpush(key, json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str))
        client.ltrim(key, -MAX_ANALYSIS_CONTEXT_EVENTS, -1)
        client.expire(key, STATE_ANALYSIS_TTL_SECONDS)
        return
    entries = ANALYSIS_CONTEXT_STORAGE.setdefault(scoped, [])
    entries.append(row)
    if len(entries) > MAX_ANALYSIS_CONTEXT_EVENTS:
        ANALYSIS_CONTEXT_STORAGE[scoped] = entries[-MAX_ANALYSIS_CONTEXT_EVENTS:]


def get_execution_events(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped:
        return []
    client = _redis()
    if client:
        key = _rkey("exec", scoped)
        raw = client.lrange(key, 0 if limit <= 0 else -limit, -1)
        out: List[Dict[str, Any]] = []
        for item in raw:
            try:
                value = json.loads(item)
                if isinstance(value, dict):
                    out.append(value)
            except Exception:
                continue
        return out
    rows = EXECUTION_STORAGE.get(scoped, [])
    if limit <= 0:
        return list(rows)
    return list(rows[-limit:])


def get_analysis_context(session_id: str, limit: int = 6) -> List[Dict[str, Any]]:
    scoped = get_scoped_session_key(session_id=session_id)
    if not scoped:
        return []
    client = _redis()
    if client:
        key = _rkey("analysis", scoped)
        raw = client.lrange(key, 0 if limit <= 0 else -limit, -1)
        out: List[Dict[str, Any]] = []
        for item in raw:
            try:
                value = json.loads(item)
                if isinstance(value, dict):
                    out.append(value)
            except Exception:
                continue
        return out
    rows = ANALYSIS_CONTEXT_STORAGE.get(scoped, [])
    if limit <= 0:
        return list(rows)
    return list(rows[-limit:])

def save_map_for_session(map_data: Dict, map_type: str = "default"):
    """
    Saves a map by type (crash, workzone, traffic, etc.).
    Multiple map types can be stored and will be merged when retrieved.
    """
    global LAST_GENERATED_MAP

    if isinstance(map_data, dict):
        map_data = sanitize_map_payload(map_data)

    scoped = get_scoped_session_key()
    client = _redis()

    # ALWAYS save to the safety net by type
    LAST_GENERATED_MAP[map_type] = map_data

    if scoped:
        if client:
            key = _rkey("map:types", scoped)
            client.hset(
                key,
                map_type,
                json.dumps(map_data, separators=(",", ":"), ensure_ascii=False, default=str),
            )
            client.expire(key, STATE_MAP_TTL_SECONDS)
        # Initialize session storage if needed
        if scoped not in MAP_STORAGE:
            MAP_STORAGE[scoped] = {}
        # Save map by type
        logger.info(f"🗺️ Saving {map_type} map for Session: {scoped}")
        MAP_STORAGE[scoped][map_type] = map_data
    else:
        logger.warning(f"⚠️ Session ID lost in thread! Saved {map_type} to Safety Net.")

def _merge_maps(maps_by_type: Dict[str, Any]) -> Optional[Dict]:
    """
    Merge multiple map payloads (crash, workzone, traffic) into a single combined payload.
    Points and lines from all sources are combined with dataset type tagging.
    """
    if not maps_by_type:
        return None

    all_points = []
    all_lines = []
    labels = []
    total_count = 0
    extra_fields: Dict[str, Any] = {}
    road_aggregate_filter: Optional[Dict[str, Any]] = None
    overlay_flag: Optional[bool] = None

    for map_type, map_data in maps_by_type.items():
        if not map_data:
            continue

        # Extract and tag points
        points = map_data.get("points", [])
        for p in points:
            p["datasetType"] = map_type
        all_points.extend(points)

        # Extract and tag lines
        lines = map_data.get("lines", [])
        for line in lines:
            line["datasetType"] = map_type
        all_lines.extend(lines)

        # Collect label info
        if map_data.get("label"):
            labels.append(map_data["label"])
        total_count += map_data.get("count", 0)

        # Preserve road scope; prefer payloads that include segment ids for map tiles.
        candidate_filter = map_data.get("roadAggregateFilter")
        if isinstance(candidate_filter, dict):
            if not road_aggregate_filter:
                road_aggregate_filter = candidate_filter
            else:
                existing_ids = road_aggregate_filter.get("road_segment_ids") or []
                candidate_ids = candidate_filter.get("road_segment_ids") or []
                if len(candidate_ids) > len(existing_ids):
                    road_aggregate_filter = candidate_filter

        if "overlay" in map_data:
            this_overlay = bool(map_data.get("overlay"))
            # If any contributor requests overlay=True, keep it true.
            if overlay_flag is None:
                overlay_flag = this_overlay
            elif this_overlay:
                overlay_flag = True

        for key, value in map_data.items():
            if key in {"label", "count", "points", "lines", "roadAggregateFilter", "overlay"}:
                continue
            if key not in extra_fields:
                extra_fields[key] = value

    if not all_points and not all_lines:
        # Return the first non-empty map if no points/lines (might have other data)
        for map_data in maps_by_type.values():
            if map_data:
                return map_data
        return None

    combined = {
        "label": " + ".join(labels) if labels else "Combined Map",
        "count": total_count,
    }

    if all_points:
        combined["points"] = all_points
    if all_lines:
        combined["lines"] = all_lines
    if road_aggregate_filter:
        combined["roadAggregateFilter"] = road_aggregate_filter
    if overlay_flag is not None:
        combined["overlay"] = overlay_flag
    if extra_fields:
        combined.update(extra_fields)

    return sanitize_map_payload(combined)


def get_and_clear_map(session_id: str) -> Dict:
    """
    Retrieves and merges all maps for the session.
    STRATEGY:
    1. Try specific session slot (may have multiple map types).
    2. If empty, check the Safety Net.
    3. Merge all map types into a single combined payload.
    """
    global LAST_GENERATED_MAP

    # Strategy 1: Session-specific maps
    scoped = get_scoped_session_key(session_id=session_id)
    client = _redis()
    maps_by_type = None
    if client and scoped:
        key = _rkey("map:types", scoped)
        raw = client.hgetall(key) or {}
        if raw:
            maps_by_type = {}
            for k, v in raw.items():
                try:
                    value = json.loads(v)
                    if isinstance(value, dict):
                        maps_by_type[k] = value
                except Exception:
                    continue
            client.delete(key)
    if maps_by_type is None:
        maps_by_type = MAP_STORAGE.pop(scoped, None) if scoped else None
    if maps_by_type:
        merged = _merge_maps(maps_by_type)
        if merged:
            if client and scoped:
                client.set(
                    _rkey("map:last", scoped),
                    json.dumps(
                        merged,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        default=str,
                    ),
                    ex=STATE_MAP_TTL_SECONDS,
                )
            if scoped:
                LAST_RETURNED_MAP[scoped] = merged
            # Clear safety-net after a successful session-scoped return
            # to avoid leaking stale maps into later unrelated requests.
            LAST_GENERATED_MAP = {}
            logger.info(f"🗺️ Merged {len(maps_by_type)} map type(s) for Session: {scoped or session_id}")
            return sanitize_map_payload(merged)

    # Strategy 2: Global Safety Net
    if LAST_GENERATED_MAP:
        logger.info(f"🛟 Recovered map from Safety Net for {scoped or session_id}")
        merged = _merge_maps(LAST_GENERATED_MAP)
        LAST_GENERATED_MAP = {}  # Clear it so we don't reuse it
        if merged:
            if scoped:
                LAST_RETURNED_MAP[scoped] = merged
            return sanitize_map_payload(merged)

    return None


def get_last_map(session_id: str) -> Dict:
    """
    Return the last map sent for this session (if any), without clearing it.
    """
    scoped = get_scoped_session_key(session_id=session_id)
    client = _redis()
    if client and scoped:
        raw = client.hgetall(_rkey("map:types", scoped)) or {}
        if raw:
            maps_by_type: Dict[str, Any] = {}
            for k, v in raw.items():
                try:
                    value = json.loads(v)
                    if isinstance(value, dict):
                        maps_by_type[k] = value
                except Exception:
                    continue
            if maps_by_type:
                return _merge_maps(maps_by_type)
        last_raw = client.get(_rkey("map:last", scoped))
        if last_raw:
            try:
                parsed = json.loads(last_raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    maps_by_type = MAP_STORAGE.get(scoped) if scoped else None
    if maps_by_type:
        return _merge_maps(maps_by_type)
    data = LAST_RETURNED_MAP.get(scoped) if scoped else None
    if data:
        return data
    if LAST_GENERATED_MAP:
        return _merge_maps(LAST_GENERATED_MAP)
    return None


def acquire_chat_lock(scoped_session_key: str, ttl_seconds: Optional[int] = None) -> Optional[str]:
    ttl = int(ttl_seconds or STATE_CHAT_TTL_SECONDS)
    token = secrets.token_hex(16)
    client = _redis()
    if client:
        acquired = client.set(
            _rkey("chat:lock", scoped_session_key),
            token,
            nx=True,
            ex=ttl,
        )
        return token if acquired else None
    if scoped_session_key in _CHAT_LOCKS:
        return None
    _CHAT_LOCKS[scoped_session_key] = token
    return token


def release_chat_lock(scoped_session_key: str, token: Optional[str]) -> None:
    if not token:
        return
    client = _redis()
    if client:
        key = _rkey("chat:lock", scoped_session_key)
        current = client.get(key)
        if current == token:
            client.delete(key)
        return
    if _CHAT_LOCKS.get(scoped_session_key) == token:
        _CHAT_LOCKS.pop(scoped_session_key, None)


def get_chat_inflight_question(scoped_session_key: str) -> str:
    client = _redis()
    if client:
        return (client.get(_rkey("chat:inflight", scoped_session_key)) or "").strip().lower()
    return (_CHAT_INFLIGHT.get(scoped_session_key) or "").strip().lower()


def set_chat_inflight_question(scoped_session_key: str, question: str, ttl_seconds: Optional[int] = None) -> None:
    q = (question or "").strip().lower()
    ttl = int(ttl_seconds or STATE_CHAT_TTL_SECONDS)
    client = _redis()
    if client:
        if q:
            client.set(_rkey("chat:inflight", scoped_session_key), q, ex=ttl)
        else:
            client.delete(_rkey("chat:inflight", scoped_session_key))
        return
    if q:
        _CHAT_INFLIGHT[scoped_session_key] = q
    else:
        _CHAT_INFLIGHT.pop(scoped_session_key, None)


def clear_chat_inflight_question(scoped_session_key: str) -> None:
    set_chat_inflight_question(scoped_session_key, "")

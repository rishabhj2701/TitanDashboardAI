"""
Schema and dataset catalog cache for the ReAct pipeline.

Separated from pipeline_react.py so caching concerns can be tested
and cleared independently of agent execution logic.
"""
from __future__ import annotations

import time

from .session_state import get_scoped_session_key

# Keyed by "\x00"-joined (scoped_session_key, domain, dataset_id_or_active).
# Null byte chosen because it cannot appear in domain names or session IDs.
_SCHEMA_CACHE: dict[str, tuple[float, str]] = {}  # value: (timestamp, schema_json)
_MAX_SCHEMA_CACHE_ENTRIES = 500
_SCHEMA_CACHE_TTL_SECONDS = 60.0

# (timestamp, catalog_string) per scoped session key
_CATALOG_CACHE: dict[str, tuple[float, str]] = {}
_CATALOG_TTL_SECONDS = 30.0


def _schema_cache_key(domain: str, dataset_id: str | None) -> str:
    scoped = get_scoped_session_key() or "__nosession__"
    did = (dataset_id or "").strip() or "__active__"
    return f"{scoped}\x00{domain}\x00{did}"


def _store_schema(key: str, value: str) -> None:
    """Insert into schema cache with a simple overflow guard."""
    if len(_SCHEMA_CACHE) >= _MAX_SCHEMA_CACHE_ENTRIES:
        _SCHEMA_CACHE.clear()
    _SCHEMA_CACHE[key] = (time.monotonic(), value)


def clear_schema_cache_for_session(session_id: str | None = None) -> None:
    """Clear all cached schemas and catalog for the given session."""
    scoped = get_scoped_session_key(session_id=session_id) or ""
    if not scoped:
        return
    prefix = f"{scoped}\x00"
    for k in [k for k in _SCHEMA_CACHE if k.startswith(prefix)]:
        del _SCHEMA_CACHE[k]
    _CATALOG_CACHE.pop(scoped, None)

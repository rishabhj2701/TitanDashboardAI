"""Tests for dynamic_analyst/caching.py (CLEAN-01 extraction)."""
from __future__ import annotations

import time
from unittest.mock import patch

from dynamic_analyst import caching


def test_clear_schema_cache_import_compat():
    """CLEAN-01: clear_schema_cache_for_session importable from pipeline_react."""
    from dynamic_analyst.pipeline_react import clear_schema_cache_for_session as pr_fn
    from dynamic_analyst.caching import clear_schema_cache_for_session as cache_fn
    assert pr_fn is cache_fn, "Re-export must return same function object"


def test_clear_schema_cache_session_scoped():
    """CLEAN-01: Only keys matching the target session are cleared."""
    caching._SCHEMA_CACHE.clear()
    caching._CATALOG_CACHE.clear()

    # Populate cache with two sessions
    caching._SCHEMA_CACHE["userA::sess1\x00traffic\x00__active__"] = (time.monotonic(), '{"col": "a"}')
    caching._SCHEMA_CACHE["userA::sess2\x00traffic\x00__active__"] = (time.monotonic(), '{"col": "b"}')
    caching._CATALOG_CACHE["userA::sess1"] = (time.monotonic(), "catalog1")
    caching._CATALOG_CACHE["userA::sess2"] = (time.monotonic(), "catalog2")

    # Clear session 1 only
    with patch.object(caching, "get_scoped_session_key", return_value="userA::sess1"):
        caching.clear_schema_cache_for_session("sess1")

    # Session 1 cleared, session 2 intact
    assert "userA::sess1\x00traffic\x00__active__" not in caching._SCHEMA_CACHE
    assert "userA::sess2\x00traffic\x00__active__" in caching._SCHEMA_CACHE
    assert "userA::sess1" not in caching._CATALOG_CACHE
    assert "userA::sess2" in caching._CATALOG_CACHE

    # Cleanup
    caching._SCHEMA_CACHE.clear()
    caching._CATALOG_CACHE.clear()


def test_store_schema_overflow_clears():
    """CLEAN-01: _store_schema clears cache when max entries reached."""
    caching._SCHEMA_CACHE.clear()
    original_max = caching._MAX_SCHEMA_CACHE_ENTRIES

    try:
        caching._MAX_SCHEMA_CACHE_ENTRIES = 3
        caching._store_schema("k1", "v1")
        caching._store_schema("k2", "v2")
        caching._store_schema("k3", "v3")
        assert len(caching._SCHEMA_CACHE) == 3

        # 4th insert triggers clear, then insert
        caching._store_schema("k4", "v4")
        assert len(caching._SCHEMA_CACHE) == 1
        assert "k4" in caching._SCHEMA_CACHE
    finally:
        caching._MAX_SCHEMA_CACHE_ENTRIES = original_max
        caching._SCHEMA_CACHE.clear()


def test_schema_cache_key_format():
    """CLEAN-01: _schema_cache_key produces expected null-byte-separated format."""
    with patch.object(caching, "get_scoped_session_key", return_value="user1::session1"):
        key = caching._schema_cache_key("traffic", "dataset_abc")
        assert key == "user1::session1\x00traffic\x00dataset_abc"

        key_no_dataset = caching._schema_cache_key("crash", None)
        assert key_no_dataset == "user1::session1\x00crash\x00__active__"


def test_clear_schema_cache_noop_when_no_session():
    """CLEAN-01: clear_schema_cache_for_session is a no-op when session key is empty."""
    caching._SCHEMA_CACHE.clear()
    caching._SCHEMA_CACHE["some\x00key"] = (time.monotonic(), "val")

    with patch.object(caching, "get_scoped_session_key", return_value=""):
        caching.clear_schema_cache_for_session("nonexistent")

    # Nothing cleared
    assert len(caching._SCHEMA_CACHE) == 1
    caching._SCHEMA_CACHE.clear()

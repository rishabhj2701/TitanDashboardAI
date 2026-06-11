import importlib
import sys

import pytest


def _import_fresh_tile_server():
    sys.modules.pop("tile_server", None)
    importlib.invalidate_caches()
    return importlib.import_module("tile_server")


def test_tile_server_preflight_allows_dev_without_postgis(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("POSTGIS_DSN", "")
    monkeypatch.setenv("REQUEST_TIMING_INCLUDE_QUERY", "0")

    module = _import_fresh_tile_server()
    assert module.app is not None


def test_tile_server_preflight_rejects_missing_postgis_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSTGIS_DSN", "")
    monkeypatch.setenv("REQUEST_TIMING_INCLUDE_QUERY", "0")

    with pytest.raises(RuntimeError, match="POSTGIS_DSN must be configured"):
        _import_fresh_tile_server()


def test_tile_server_preflight_rejects_query_logging_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=postgis port=5432")
    monkeypatch.setenv("REQUEST_TIMING_INCLUDE_QUERY", "1")

    with pytest.raises(RuntimeError, match="REQUEST_TIMING_INCLUDE_QUERY must be disabled"):
        _import_fresh_tile_server()


def test_tile_server_preflight_accepts_valid_production_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=postgis port=5432")
    monkeypatch.setenv("REQUEST_TIMING_INCLUDE_QUERY", "0")

    module = _import_fresh_tile_server()
    assert module.app is not None

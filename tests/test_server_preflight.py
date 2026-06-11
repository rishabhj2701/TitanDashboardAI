import importlib
import sys

import pytest


def _import_fresh_server():
    sys.modules.pop("server", None)
    importlib.invalidate_caches()
    return importlib.import_module("server")


def test_server_preflight_allows_dev_with_missing_prod_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("POSTGIS_DSN", "")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    monkeypatch.setenv("REQUIRE_USER_ID", "")

    module = _import_fresh_server()
    assert module.app is not None


def test_server_preflight_rejects_missing_jwt_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=postgis port=5432")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("REQUIRE_USER_ID", "1")

    with pytest.raises(RuntimeError, match="JWT_SECRET must be configured"):
        _import_fresh_server()


def test_server_preflight_rejects_wildcard_cors_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "strong-secret")
    monkeypatch.setenv("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=postgis port=5432")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
    monkeypatch.setenv("REQUIRE_USER_ID", "1")

    with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS must be explicit"):
        _import_fresh_server()


def test_server_preflight_accepts_valid_production_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "strong-secret")
    monkeypatch.setenv("POSTGIS_DSN", "dbname=traffic user=postgres password=postgres host=postgis port=5432")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("REQUIRE_USER_ID", "1")

    module = _import_fresh_server()
    assert module.app is not None

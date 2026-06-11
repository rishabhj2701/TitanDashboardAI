from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight.sh"


def _run_preflight(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "JWT_SECRET": "strong-secret",
            "CORS_ALLOW_ORIGINS": "https://app.example.com",
            "POSTGIS_DSN": "postgresql://user:pass@db:5432/traffic",
            "REQUIRE_USER_ID": "1",
            "ALLOW_DEBUG_USER_HEADER": "0",
            "REQUEST_TIMING_INCLUDE_QUERY": "0",
        }
    )
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_preflight_rejects_debug_user_header_in_production():
    result = _run_preflight({"ALLOW_DEBUG_USER_HEADER": "1"})
    assert result.returncode == 1
    assert "ALLOW_DEBUG_USER_HEADER must be disabled in production." in result.stderr


def test_preflight_rejects_explicit_tile_wildcard_cors():
    result = _run_preflight({"TILE_CORS_ALLOW_ORIGINS": "*"})
    assert result.returncode == 1
    assert "TILE_CORS_ALLOW_ORIGINS cannot include '*'" in result.stderr


def test_preflight_allows_valid_config_and_emits_tile_warning_when_unset():
    result = _run_preflight({})
    assert result.returncode == 0
    assert "Preflight warning(s):" in result.stdout
    assert "TILE_CORS_ALLOW_ORIGINS is unset" in result.stdout

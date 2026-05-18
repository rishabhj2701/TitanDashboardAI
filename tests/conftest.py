"""Shared fixtures for the evaluation harness."""

import os
import json
import uuid
import pytest
import httpx
import time
from pathlib import Path

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data"

# Server configuration — override with env vars for CI.
# If not explicitly set, prefer a live local API endpoint.
def _is_reachable(base_url: str, timeout_s: float = 1.0) -> bool:
    probe = f"{base_url.rstrip('/')}/api/datasets"
    try:
        resp = httpx.get(probe, timeout=timeout_s)
        return resp.status_code < 500
    except Exception:
        return False


def _resolve_base_url() -> str:
    explicit = os.environ.get("EVAL_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    for candidate in ("http://localhost:8000", "http://localhost:8080"):
        if _is_reachable(candidate):
            return candidate
    return "http://localhost:8000"


BASE_URL = _resolve_base_url()
UPLOAD_ENDPOINT = f"{BASE_URL}/api/upload"
CHAT_ENDPOINT = f"{BASE_URL}/api/chat"
KPI_ENDPOINT = f"{BASE_URL}/api/kpis"
CLEAR_ENDPOINT = f"{BASE_URL}/api/chat/clear"

CHAT_TIMEOUT = 120  # seconds per request


@pytest.fixture(scope="session")
def http_client():
    """Shared httpx client for all tests."""
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        try:
            probe = client.get(f"{BASE_URL}/api/datasets")
            if probe.status_code >= 500:
                pytest.skip(
                    f"Eval API unhealthy at {BASE_URL} (status={probe.status_code}). "
                    "Set EVAL_BASE_URL to a healthy app API endpoint."
                )
        except Exception as exc:
            pytest.skip(
                f"Eval API not reachable at {BASE_URL}: {exc}. "
                "Set EVAL_BASE_URL (for Docker web use http://localhost:8080)."
            )
        yield client


@pytest.fixture(scope="module")
def session_id():
    """Unique session ID per test module."""
    return f"eval_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def clean_session(http_client, session_id):
    """Ensure a fresh session at module start."""
    try:
        http_client.post(CLEAR_ENDPOINT, json={"sessionId": session_id})
    except Exception:
        pass  # server may not support clear yet
    return session_id


# ── Helpers (importable by test files) ─────────────────────────────

def upload_sample_data(client: httpx.Client, session_id: str, filename: str) -> dict:
    """Upload a sample CSV and return the response."""
    filepath = SAMPLE_DATA_DIR / filename
    with open(filepath, "rb") as f:
        resp = client.post(
            UPLOAD_ENDPOINT,
            files={"file": (filename, f, "text/csv")},
            data={"sessionId": session_id},
        )
    resp.raise_for_status()
    return resp.json()


def send_chat(client: httpx.Client, session_id: str, message: str) -> dict:
    """Send a chat message and return the full response dict + elapsed_ms."""
    t0 = time.time()
    resp = client.post(
        CHAT_ENDPOINT,
        json={"message": message, "sessionId": session_id},
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    resp.raise_for_status()
    data = resp.json()
    data["_elapsed_ms"] = elapsed_ms
    return data


def load_scenarios(domain: str) -> list:
    """Load scenario JSON for a given domain."""
    path = SCENARIOS_DIR / f"{domain}.json"
    with open(path) as f:
        return json.load(f)


def get_kpis(client: httpx.Client) -> dict:
    """Get current KPI counters."""
    resp = client.get(KPI_ENDPOINT)
    resp.raise_for_status()
    return resp.json()

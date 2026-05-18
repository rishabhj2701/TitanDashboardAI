import ipaddress

import pytest
from fastapi import HTTPException

from backend.services import uploads as uploads_service


def test_validate_upload_url_allows_supported_scheme(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOWED_SCHEMES", ("http", "https"))
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOW_PRIVATE_NET", True)

    parsed = uploads_service._validate_upload_url("https://example.com/data.csv")
    assert parsed.scheme == "https"
    assert parsed.netloc == "example.com"


def test_validate_upload_url_rejects_disallowed_scheme(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOWED_SCHEMES", ("https",))
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOW_PRIVATE_NET", True)

    with pytest.raises(ValueError, match="not allowed"):
        uploads_service._validate_upload_url("http://example.com/data.csv")


def test_validate_upload_url_rejects_private_network_target(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOWED_SCHEMES", ("https",))
    monkeypatch.setattr(uploads_service, "UPLOAD_URL_ALLOW_PRIVATE_NET", False)
    monkeypatch.setattr(
        uploads_service,
        "_resolved_ips",
        lambda _host: [ipaddress.ip_address("10.0.0.8")],
    )

    with pytest.raises(ValueError, match="non-public"):
        uploads_service._validate_upload_url("https://internal.local/data.csv")


def test_enforce_upload_size_limit_raises_413(monkeypatch):
    monkeypatch.setattr(uploads_service, "UPLOAD_MAX_BYTES", 3)

    with pytest.raises(HTTPException) as exc:
        uploads_service._enforce_upload_size_limit(4)

    assert exc.value.status_code == 413

"""Regression tests for PR #46 review findings L-1 and L-5 (Issue #62).

L-1: invalid Python Unicode (lone surrogates) in a bearer value must fail
     closed with 401 and never propagate UnicodeEncodeError.
L-5: a configured JOB_ADMIN_TOKEN that is not ASCII token68 cannot be sent
     reliably over HTTP and must fail closed with a deliberate 503, without
     ever echoing the configured secret.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from job_admin_auth import authorize_job_admin, job_admin_token_configured
from main import app
from runtime_security import get_runtime_security_status


TOKEN = "Va1id-Token_68.~+/" + "k" * 30 + "=="
SURROGATE_VALUES = [
    "Bearer \ud800",
    "Bearer \udfff",
    "Bearer \udcff",  # os.environ surrogateescape form of byte 0xff
    "Bearer " + TOKEN[:-1] + "\ud800",
    "Bearer " + TOKEN + "\ud800",
    "\ud800 " + TOKEN,
    "Bearer " + chr(0xD83D) + chr(0xDD12),  # two lone surrogates, not a real code point
]
NON_TOKEN68_CONFIG = [
    "caf" + chr(0xE9) + "x" * 40,              # non-ASCII latin-1
    chr(0x2603) * 40,                     # non-latin-1
    "x" * 40 + "\ud800",                 # lone surrogate
    "x" * 40 + "\udcff",                 # surrogateescape from undecodable env bytes
    "x" * 20 + " " + "x" * 20,           # embedded space
    "x" * 20 + "\t" + "x" * 20,          # embedded control
    "x" * 20 + "\x00" + "x" * 20,        # NUL
    "x" * 20 + ":" + "x" * 20,           # punctuation outside token68
    "=" + "x" * 40,                      # padding not at end
    "x" * 20 + "=" + "x" * 20,           # padding in middle
]


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)


# ---- L-1: direct-call negative paths -------------------------------------

@pytest.mark.parametrize("value", SURROGATE_VALUES)
def test_l1_lone_surrogate_bearer_is_401_not_unicode_error(enabled, value):
    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(value)
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("value", [
    b"Bearer " + TOKEN.encode(), 12345, ["Bearer", TOKEN], object(),
])
def test_l1_non_string_authorization_fails_closed(enabled, value):
    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(value)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("value", [
    "Bearer " + TOKEN + " extra",
    "Bearer " + TOKEN[:-3],
    "Basic " + TOKEN,
    "Bearer",
    "Bearer ",
    TOKEN,
])
def test_l1_ordinary_invalid_tokens_still_401(enabled, value):
    with pytest.raises(HTTPException) as exc:
        authorize_job_admin(value)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("value", [
    "Bearer " + TOKEN,
    "bearer " + TOKEN,
    "BEARER  " + TOKEN + "  ",
])
def test_l1_valid_token_still_authorized(enabled, value):
    assert authorize_job_admin(value) is True


def test_l1_disabled_gate_still_wins_over_surrogate(monkeypatch):
    monkeypatch.delenv("ENABLE_JOB_ADMIN_ENDPOINTS", raising=False)
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as exc:
        authorize_job_admin("Bearer \ud800")
    assert exc.value.status_code == 503


# ---- L-5: configuration negative paths -----------------------------------

@pytest.mark.parametrize("configured", NON_TOKEN68_CONFIG)
def test_l5_non_token68_configured_token_is_503(configured):
    env = {"ENABLE_JOB_ADMIN_ENDPOINTS": "true", "JOB_ADMIN_TOKEN": configured}
    # os.environ rejects some of these values (NUL, lone surrogates), but a
    # real process can still surface them (e.g. surrogateescape), so the
    # config read itself is substituted.
    with patch("job_admin_auth.os.getenv",
               side_effect=lambda key, default=None: env.get(key, default)):
        assert job_admin_token_configured() is False
        for presented in ("Bearer " + configured, "Bearer " + TOKEN, None):
            with pytest.raises(HTTPException) as exc:
                authorize_job_admin(presented)
            assert exc.value.status_code == 503
            assert exc.value.detail == "Job administration is not configured"
            assert exc.value.headers is None


@pytest.mark.parametrize("configured", ["", "   ", "short", "x" * 31])
def test_l5_missing_or_short_token_still_503(monkeypatch, configured):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", configured)
    assert job_admin_token_configured() is False
    with pytest.raises(HTTPException) as exc:
        authorize_job_admin("Bearer " + configured)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("configured", [
    "x" * 32, TOKEN, "  " + TOKEN + "  ", "A1b2" * 12 + "=",
])
def test_l5_valid_token68_configuration_accepted(monkeypatch, configured):
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", configured)
    assert job_admin_token_configured() is True
    assert authorize_job_admin("Bearer " + configured.strip()) is True


def test_l5_runtime_status_reports_config_without_secret(monkeypatch):
    bad = "caf" + chr(0xE9) + "s" * 40
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", bad)
    status = get_runtime_security_status()
    assert status["admin_token_configured"] is False
    assert bad not in repr(status)
    monkeypatch.setenv("JOB_ADMIN_TOKEN", TOKEN)
    status = get_runtime_security_status()
    assert status["admin_token_configured"] is True
    assert TOKEN not in repr(status)


# ---- HTTP-level negative paths (real ASGI header decoding) ---------------

def _client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("raw", [
    b"Bearer caf\xc3\xa9",
    b"Bearer \xed\xa0\x80",            # UTF-8-encoded surrogate (invalid UTF-8)
    b"Bearer \xff\xfe",
    b"Bearer " + TOKEN.encode() + b"\xc3\xa9",
])
def test_http_invalid_bytes_bearer_is_401_and_storage_untouched(enabled, raw):
    with patch("job_admin_routes.job_management.list_jobs") as listing:
        response = _client().get("/admin/jobs",
                                 headers=[(b"authorization", raw)])
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    listing.assert_not_called()


def test_http_non_ascii_configured_token_is_503_without_leak(monkeypatch):
    configured = "caf" + chr(0xE9) + "n" * 40
    monkeypatch.setenv("ENABLE_JOB_ADMIN_ENDPOINTS", "true")
    monkeypatch.setenv("JOB_ADMIN_TOKEN", configured)
    with patch("job_admin_routes.job_management.list_jobs") as listing:
        for raw in (b"Bearer " + configured.encode("utf-8"),
                    b"Bearer " + configured.encode("latin-1")):
            response = _client().get("/admin/jobs",
                                     headers=[(b"authorization", raw)])
            assert response.status_code == 503
            assert response.json() == {
                "detail": "Job administration is not configured"}
            assert configured.encode("utf-8") not in response.content
            assert "n" * 40 not in response.text
    listing.assert_not_called()


def test_http_valid_token_still_reaches_handler(enabled):
    with patch("job_admin_routes.job_management.list_jobs",
               return_value={"jobs": [], "next_cursor": None}) as listing:
        response = _client().get(
            "/admin/jobs", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    listing.assert_called_once()

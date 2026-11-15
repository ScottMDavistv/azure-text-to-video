import hashlib
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException

from project_oidc import (
    ProjectIdentityError, oidc_config, verify_project_token,
)
from project_api_routes import project_holds_endpoint, cancel_project_hold_endpoint


def uid():
    return str(uuid4())


def test_oidc_disabled_by_default(monkeypatch):
    for name in ("PROJECT_OIDC_ISSUER", "PROJECT_OIDC_AUDIENCE",
                 "PROJECT_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ProjectIdentityError):
        oidc_config()


def test_oidc_rejects_jwks_from_different_host(monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", "https://login.example.test/tenant")
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", "render-api")
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", "https://attacker.test/keys")
    with pytest.raises(ProjectIdentityError):
        oidc_config()


def test_verified_token_maps_to_server_side_user_id(monkeypatch):
    issuer = "https://identity.example.test/tenant"
    subject = "external-user-123"
    user_id = uid()
    token = "signed-token"
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", issuer)
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", "render-api")
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", "https://identity.example.test/keys")
    table = MagicMock()
    table.get_entity.return_value = {"active": True, "userId": user_id}

    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = MagicMock(key="public-key")
    with patch("project_oidc.jwt.decode", return_value={"sub": subject}) as decode:
        actor = verify_project_token(
            token, table, jwks_client_factory=lambda _: client
        )
    assert actor.user_id == user_id
    assert table.get_entity.call_args.kwargs["row_key"] == hashlib.sha256(
        (issuer + "|" + subject).encode()
    ).hexdigest()
    assert decode.call_args.kwargs["algorithms"] == ["RS256"]


def test_unregistered_oidc_identity_rejected(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", "https://identity.example.test/t")
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", "render-api")
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", "https://identity.example.test/keys")
    table = MagicMock()
    table.get_entity.side_effect = ResourceNotFoundError("missing")
    client = MagicMock()
    with patch("project_oidc.jwt.decode", return_value={"sub": "subject"}):
        with pytest.raises(ProjectIdentityError):
            verify_project_token("token", table, jwks_client_factory=lambda _: client)


def test_project_routes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PROJECT_ROUTES", raising=False)
    with pytest.raises(HTTPException) as exc:
        project_holds_endpoint(uid(), authorization="Bearer invalid")
    assert exc.value.status_code == 503


def test_cancel_route_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PROJECT_ROUTES", raising=False)
    with pytest.raises(HTTPException) as exc:
        cancel_project_hold_endpoint(uid(), uid(), authorization="Bearer invalid")
    assert exc.value.status_code == 503

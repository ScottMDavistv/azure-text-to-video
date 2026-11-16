"""B1 Entra sign-in and project-scoped API security regression tests."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from azure.core.exceptions import ResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from project_access import Actor, Action, AccessDenied
from project_models import Project, ProjectRole
from project_oidc import (
    ProjectIdentityError, ProjectScopeDenied,
    oidc_config, verify_project_token, validate_delegated_scope,
)
from project_api_routes import (
    verified_request_actor, project_holds_endpoint, cancel_project_hold_endpoint,
)

TENANT = "773e9051-c546-4b75-9432-90f4f8242660"
APPLICATION = "a5a09b74-aead-455f-9de7-9b844c8a8465"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
AUDIENCE = f"api://{APPLICATION}"
JWKS = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"


def uid():
    return str(uuid4())


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", JWKS)
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", AUDIENCE)
    return monkeypatch


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(**changes):
    now = datetime.now(timezone.utc)
    base = {
        "iss": ISSUER, "aud": AUDIENCE, "tid": TENANT,
        "sub": "subject-123", "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "scp": "openid access_as_user profile",
    }
    base.update(changes)
    return base


def signed_token(key, payload):
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "b1-test"})


def verify_signed(configured, signing_key, table=None, payload=None, token=None):
    table = table or MagicMock()
    user_id = uid()
    table.get_entity.return_value = {"active": True, "userId": user_id}
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = MagicMock(key=signing_key.public_key())
    if token is None:
        token = signed_token(signing_key, payload or claims())
    return verify_project_token(
        token, table, jwks_client_factory=lambda _: jwks,
    ), table, user_id


def test_oidc_disabled_by_default(monkeypatch):
    for name in ("PROJECT_OIDC_ISSUER", "PROJECT_OIDC_AUDIENCE",
                 "PROJECT_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ProjectIdentityError):
        oidc_config()


def test_oidc_rejects_jwks_from_different_host(configured, monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", "https://attacker.test/keys")
    with pytest.raises(ProjectIdentityError):
        oidc_config()


def test_oidc_rejects_wrong_environments(configured, monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", "https://login.microsoftonline.com/common/v2.0")
    with pytest.raises(ProjectIdentityError):
        oidc_config()
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", "https://api.example.com")
    with pytest.raises(ProjectIdentityError):
        oidc_config()


def test_valid_signed_access_token_maps_to_registered_user(configured, signing_key):
    actor, table, user_id = verify_signed(configured, signing_key)
    assert actor == Actor(user_id, True)
    assert table.get_entity.call_args.kwargs["row_key"] == hashlib.sha256(
        (ISSUER + "|subject-123").encode()
    ).hexdigest()


@pytest.mark.parametrize("payload", [
    claims(scp="read_only"),
    claims(scp="access_as_user_extra"),
    claims(scp=""),
    claims(scp=["access_as_user"]),
    claims(roles=["access_as_user"], scp=None),
])
def test_signed_token_without_exact_delegated_scope_denied(
    configured, signing_key, payload,
):
    with pytest.raises(ProjectScopeDenied):
        verify_signed(configured, signing_key, payload=payload)


@pytest.mark.parametrize("payload", [
    claims(tid=uid()),
    claims(iss="https://login.microsoftonline.com/" + str(uuid4()) + "/v2.0"),
    claims(aud="api://" + str(uuid4())),
    claims(exp=0),
])
def test_wrong_tenant_issuer_audience_or_expiry_rejected(
    configured, signing_key, payload,
):
    with pytest.raises(ProjectIdentityError):
        verify_signed(configured, signing_key, payload=payload)


def test_modified_signature_rejected(configured, signing_key):
    token = signed_token(signing_key, claims())
    segments = token.split(".")
    forged = segments[0] + "." + segments[1] + "." + (
        "X" if segments[2][0] != "X" else "Y"
    ) + segments[2][1:]
    with pytest.raises(ProjectIdentityError):
        verify_signed(configured, signing_key, token=forged)


def test_unregistered_identity_rejected(configured, signing_key):
    table = MagicMock()
    table.get_entity.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(ProjectIdentityError):
        verify_signed(configured, signing_key, table=table)


def test_inactive_identity_rejected(configured, signing_key):
    table = MagicMock()
    table.get_entity.return_value = {"active": False, "userId": uid()}
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = MagicMock(key=signing_key.public_key())
    with pytest.raises(ProjectIdentityError):
        verify_project_token(
            signed_token(signing_key, claims()), table,
            jwks_client_factory=lambda _: jwks,
        )


def test_project_routes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PROJECT_ROUTES", raising=False)
    with pytest.raises(HTTPException) as exc:
        project_holds_endpoint(UUID(uid()), authorization="Bearer invalid")
    assert exc.value.status_code == 503


def test_cancel_route_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PROJECT_ROUTES", raising=False)
    with pytest.raises(HTTPException) as exc:
        cancel_project_hold_endpoint(UUID(uid()), UUID(uid()), authorization="Bearer invalid")
    assert exc.value.status_code == 503


def test_routes_require_bearer_token_even_if_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    with pytest.raises(HTTPException) as exc:
        verified_request_actor(MagicMock(), "Basic foo")
    assert exc.value.status_code == 401


def test_missing_scope_is_http_403(monkeypatch):
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    with patch("project_api_routes.verify_project_token",
               side_effect=ProjectScopeDenied("missing")):
        with pytest.raises(HTTPException) as exc:
            verified_request_actor(MagicMock(), "Bearer signed")
    assert exc.value.status_code == 403


def test_invalid_token_is_http_401(monkeypatch):
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    with patch("project_api_routes.verify_project_token",
               side_effect=ProjectIdentityError("bad token")):
        with pytest.raises(HTTPException) as exc:
            verified_request_actor(MagicMock(), "Bearer forged")
    assert exc.value.status_code == 401


def test_cross_project_read_denied_before_dashboard(monkeypatch):
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    project_id = uid()
    actor = Actor(uid(), True)
    with (
        patch("project_api_routes.get_jobs_table_client", return_value=MagicMock()),
        patch("project_api_routes.verified_request_actor", return_value=actor),
        patch("project_api_routes.require_project_role", side_effect=AccessDenied()),
        patch("project_api_routes.held_job_dashboard") as dashboard,
    ):
        with pytest.raises(HTTPException) as exc:
            project_holds_endpoint(UUID(project_id), authorization="Bearer signed")
    assert exc.value.status_code == 403
    dashboard.assert_not_called()


def test_cross_project_cancel_denied_before_mutation(monkeypatch):
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    actor = Actor(uid(), True)
    with (
        patch("project_api_routes.get_jobs_table_client", return_value=MagicMock()),
        patch("project_api_routes.verified_request_actor", return_value=actor),
        patch("project_api_routes.require_project_role", side_effect=AccessDenied()),
        patch("project_api_routes.cancel_held_job") as cancel,
    ):
        with pytest.raises(HTTPException) as exc:
            cancel_project_hold_endpoint(UUID(uid()), UUID(uid()), authorization="Bearer signed")
    assert exc.value.status_code == 403
    cancel.assert_not_called()


def test_revoked_project_membership_rejected(monkeypatch):
    from project_security import require_project_role
    user = Actor(uid(), True)
    project_id = uid()
    with (
        patch("project_security.get_project", return_value=Project(project_id, "Dev", user.user_id)),
        patch("project_security.get_membership_role", return_value=None),
    ):
        with pytest.raises(AccessDenied):
            require_project_role(MagicMock(), user, project_id, Action.READ)

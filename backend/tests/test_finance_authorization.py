"""Signed Entra delegated identity and server-recorded finance access tests."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from azure.core.exceptions import ResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa

from finance_authorization import (
    approve_increase_verified, provision_limit_verified,
    request_project_increase_verified,
)
from finance_controls import FinanceDenied


TENANT = "773e9051-c546-4b75-9432-90f4f8242660"
APP = "a5a09b74-aead-455f-9de7-9b844c8a8465"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def uid():
    return str(uuid4())


@pytest.fixture
def signed(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", f"api://{APP}")
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL",
                       f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys")
    now = datetime.now(timezone.utc)
    def token(name, **overrides):
        claims = {
            "iss": ISSUER, "aud": f"api://{APP}", "tid": TENANT,
            "sub": name, "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "scp": "access_as_user",
        }
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="RS256")
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = MagicMock(key=key.public_key())
    digest = lambda name: hashlib.sha256((ISSUER + "|" + name).encode()).hexdigest()
    return token, lambda _: jwks, digest


def table_for(signed, *, role="owner", active=True):
    _, _, digest = signed
    project_id, user_id = uid(), uid()
    table = MagicMock()
    def read(*, partition_key, row_key):
        if partition_key == "project-oidc-users":
            if row_key != digest("user"):
                raise ResourceNotFoundError("unknown")
            return {"active": active, "userId": user_id}
        if partition_key == "projects":
            return {"RowKey": project_id, "name": "Project", "ownerId": user_id,
                    "status": "active"}
        if partition_key.startswith("project-members:"):
            if role is None:
                raise ResourceNotFoundError("not a member")
            return {"role": role}
        if partition_key == "render-finance-v1":
            item = {"PartitionKey": partition_key, "RowKey": row_key,
                    "ceilingCents": 500, "spentCents": 0,
                    "reservedCents": 0}
            return FakeEntity(item)
        raise ResourceNotFoundError("unexpected")
    table.get_entity.side_effect = read
    return table, project_id


class FakeEntity(dict):
    @property
    def metadata(self):
        return {"etag": 'W/"one"'}


def test_verified_project_owner_may_request_increase(signed):
    token, factory, _ = signed
    table, project = table_for(signed)
    request_project_increase_verified(
        table, project_id=project, request_id=uid(), proposed_cents=1000,
        access_token=token("user"), jwks_client_factory=factory)
    table.submit_transaction.assert_called_once()


@pytest.mark.parametrize("role", [None, "member"])
def test_verified_nonowner_denied(signed, role):
    token, factory, _ = signed
    table, project = table_for(signed, role=role)
    with pytest.raises(FinanceDenied):
        request_project_increase_verified(
            table, project_id=project, request_id=uid(), proposed_cents=1000,
            access_token=token("user"), jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_forged_owner_claim_does_not_grant_membership(signed):
    token, factory, _ = signed
    table, project = table_for(signed, role="member")
    with pytest.raises(FinanceDenied):
        request_project_increase_verified(
            table, project_id=project, request_id=uid(), proposed_cents=1000,
            access_token=token("user", role="owner", roles=["owner"]),
            jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_inactive_user_mapping_denied(signed):
    token, factory, _ = signed
    table, project = table_for(signed, active=False)
    with pytest.raises(FinanceDenied):
        request_project_increase_verified(
            table, project_id=project, request_id=uid(), proposed_cents=1000,
            access_token=token("user"), jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_unregistered_admin_not_accepted(signed):
    token, factory, digest = signed
    table, _ = table_for(signed)
    with pytest.raises(FinanceDenied):
        approve_increase_verified(
            table, request_id=uid(), access_token=token("user"),
            allowed_admin_digests=frozenset([digest("admin")]),
            jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_admin_policy_must_be_trusted_nonempty_digest_set(signed):
    token, factory, digest = signed
    table, _ = table_for(signed)
    with pytest.raises(FinanceDenied):
        provision_limit_verified(
            table, scope="global:fal", ceiling_cents=1000,
            access_token=token("user"), allowed_admin_digests=frozenset(),
            jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_admin_signed_identity_allows_provision(signed):
    token, factory, digest = signed
    table, _ = table_for(signed)
    provision_limit_verified(
        table, scope="global:fal", ceiling_cents=1000,
        access_token=token("user"),
        allowed_admin_digests=frozenset([digest("user")]),
        jwks_client_factory=factory)
    table.submit_transaction.assert_called_once()


def test_invalid_signature_denied_without_storage_writes(signed):
    token, factory, digest = signed
    table, project = table_for(signed)
    jwt_text = token("user")
    parts = jwt_text.split(".")
    parts[2] = "corrupted"
    with pytest.raises(FinanceDenied):
        request_project_increase_verified(
            table, project_id=project, request_id=uid(), proposed_cents=1000,
            access_token=".".join(parts), jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_other_tenant_or_expired_token_denied(signed):
    token, factory, _ = signed
    table, project = table_for(signed)
    for invalid in (token("user", tid=uid()), token("user", exp=0)):
        with pytest.raises(FinanceDenied):
            request_project_increase_verified(
                table, project_id=project, request_id=uid(), proposed_cents=1000,
                access_token=invalid, jwks_client_factory=factory)
    table.submit_transaction.assert_not_called()


def test_two_distinct_signed_admin_identities_revalidated(signed):
    from finance_authorization import _admin_lookup
    token, factory, digest = signed
    first, second = uid(), uid()
    table = MagicMock()
    mapping = {
        digest("first"): {"active": True, "userId": first},
        digest("second"): {"active": True, "userId": second},
    }
    def get_entity(*, partition_key, row_key):
        assert partition_key == "project-oidc-users"
        if row_key not in mapping:
            raise ResourceNotFoundError("missing")
        return mapping[row_key]
    table.get_entity.side_effect = get_entity
    allowed = frozenset(mapping)
    actor, lookup = _admin_lookup(
        table, token("second"), allowed, jwks_client_factory=factory)
    assert actor.user_id == second
    assert lookup(first) is True
    assert lookup(second) is True
    mapping[digest("first")]["active"] = False
    assert lookup(first) is False
    mapping[digest("first")]["active"] = True
    mapping.pop(digest("first"))
    assert lookup(first) is False
    assert lookup(uid()) is False

"""Security regressions for the restricted two-administrator onboarding path."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import MagicMock

import jwt
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa

from first_user_onboarding import OnboardingBlocked
from restricted_onboarding import (
    approve_enrollment, execute_enrollment, identity_digest,
    propose_enrollment, trusted_admin_identity,
)
from restricted_onboarding_cli import load_admin_allowlist, main

TENANT = "773e9051-c546-4b75-9432-90f4f8242660"
APP = "a5a09b74-aead-455f-9de7-9b844c8a8465"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def uid():
    return str(uuid4())


class MemoryTable:
    def __init__(self):
        self.records = {}
        self.calls = 0
        self.fail_on = None

    def get_entity(self, *, partition_key, row_key):
        try:
            return dict(self.records[(partition_key, row_key)])
        except KeyError:
            raise ResourceNotFoundError("not found")

    def create_entity(self, *, entity):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError("simulated interrupted write")
        key = entity["PartitionKey"], entity["RowKey"]
        if key in self.records:
            raise ResourceExistsError("already exists")
        self.records[key] = dict(entity)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL", f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys")
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", f"api://{APP}")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    def token(name, **changes):
        payload = {
            "iss": ISSUER, "aud": f"api://{APP}", "tid": TENANT,
            "sub": name, "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "scp": "access_as_user",
        }
        payload.update(changes)
        return jwt.encode(payload, key, algorithm="RS256")
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = MagicMock(key=key.public_key())
    factory = lambda _: jwks
    digest = lambda name: hashlib.sha256((ISSUER + "|" + name).encode()).hexdigest()
    return token, factory, frozenset([digest("admin-a"), digest("admin-b")]), digest


def flow(setup, table=None):
    token, factory, allowlist, digest = setup
    table = table or MemoryTable()
    enrollment = uid()
    kw = {"enrollment_id": enrollment, "allowed_admin_digests": allowlist,
          "jwks_client_factory": factory}
    return table, enrollment, kw, token


def propose(table, kw, token, now=None):
    return propose_enrollment(table, **kw, target_token=token("target"),
                              proposer_token=token("admin-a"),
                              project_name="Approved Dev Project", now=now)


def approve(table, kw, token, now=None):
    return approve_enrollment(table, **kw, approver_token=token("admin-b"), now=now)


def execute(table, kw, token, now=None):
    return execute_enrollment(table, **kw, target_token=token("target"),
                              operator_token=token("admin-a"), now=now)


def test_independent_approval_and_full_execution(setup):
    table, enrollment, kw, token = flow(setup)
    propose(table, kw, token)
    approve(table, kw, token)
    result = execute(table, kw, token)
    assert result["status"] == "onboarded"
    assert len(table.records) == 7
    assert execute(table, kw, token)["replayed"] is True
    assert len(table.records) == 7


def test_no_writes_for_unknown_administrator(setup):
    table, _, kw, token = flow(setup)
    with pytest.raises(OnboardingBlocked, match="administrator"):
        propose_enrollment(table, **kw, target_token=token("target"),
                           proposer_token=token("other"),
                           project_name="Approved Dev Project")
    assert table.calls == 0


def test_target_cannot_approve_own_role_even_if_allowlisted(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    _, factory, allowlist, digest = setup
    with pytest.raises(OnboardingBlocked, match="independent"):
        approve_enrollment(table, **{**kw, "allowed_admin_digests": allowlist | {digest("target")}},
                           approver_token=token("target"))


def test_self_approval_rejected(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    with pytest.raises(OnboardingBlocked, match="independent"):
        approve_enrollment(table, **kw, approver_token=token("admin-a"))
    assert len(table.records) == 1


def test_execute_without_approval_never_creates_user(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    with pytest.raises(OnboardingBlocked, match="approval"):
        execute(table, kw, token)
    assert len(table.records) == 1


def test_approval_bound_to_target_identity(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    approve(table, kw, token)
    with pytest.raises(OnboardingBlocked, match="Target identity"):
        execute_enrollment(table, **kw, target_token=token("different"),
                           operator_token=token("admin-a"))
    assert len(table.records) == 2


def test_tampered_approval_rejected(setup):
    table, enrollment, kw, token = flow(setup)
    propose(table, kw, token)
    approve(table, kw, token)
    table.records[("first-owner-approval-v1", "approval:" + enrollment)]["projectName"] = "Other"
    with pytest.raises(OnboardingBlocked, match="match"):
        execute(table, kw, token)
    assert len(table.records) == 2


def test_expired_request_rejected_before_mapping(setup):
    table, _, kw, token = flow(setup)
    now = datetime.now(timezone.utc)
    propose(table, kw, token, now=now)
    with pytest.raises(OnboardingBlocked, match="expired"):
        approve(table, kw, token, now=now + timedelta(hours=25))
    assert len(table.records) == 1


def test_expired_execution_rejected(setup):
    table, _, kw, token = flow(setup)
    now = datetime.now(timezone.utc)
    propose(table, kw, token, now=now)
    approve(table, kw, token, now=now + timedelta(hours=1))
    with pytest.raises(OnboardingBlocked, match="expired"):
        execute(table, kw, token, now=now + timedelta(hours=25))
    assert len(table.records) == 2


def test_malformed_or_missing_admin_allowlist_rejected(setup):
    token, factory, _, _ = setup
    with pytest.raises(OnboardingBlocked):
        trusted_admin_identity(token("admin-a"), frozenset(), jwks_client_factory=factory)
    with pytest.raises(OnboardingBlocked):
        trusted_admin_identity(token("admin-a"), frozenset(["oops"]), jwks_client_factory=factory)


def test_forged_admin_token_rejected_before_writes(setup):
    table, _, kw, token = flow(setup)
    with pytest.raises(Exception):
        propose_enrollment(table, **kw, target_token=token("target"),
                           proposer_token="not.a.jwt",
                           project_name="Approved Dev Project")
    assert table.calls == 0


def test_crash_replay_preserves_single_approval(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    approve(table, kw, token)
    table.fail_on = table.calls + 3
    with pytest.raises(RuntimeError):
        execute(table, kw, token)
    table.fail_on = None
    result = execute(table, kw, token)
    assert result["status"] == "onboarded"
    assert len(table.records) == 7


def test_cli_requires_explicit_write_switch(setup, tmp_path):
    _, _, allowlist, _ = setup
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "tenantId": TENANT, "apiClientId": APP,
        "storageAccount": "sttextvideo1794671698",
        "tableName": "VideoJobs", "adminDigests": list(allowlist),
    }))
    assert load_admin_allowlist(policy) == allowlist
    with pytest.raises(SystemExit):
        main(["propose", "--admin-policy", str(policy),
              "--enrollment-id", uid(), "--project-name", "Test"],
             input_token=lambda _: pytest.fail("No token prompt allowed"),
             table_factory=lambda: pytest.fail("No Azure writes allowed"))


def test_cli_rejects_group_writable_admin_policy(setup, tmp_path):
    _, _, allowlist, _ = setup
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "tenantId": TENANT, "apiClientId": APP,
        "storageAccount": "sttextvideo1794671698",
        "tableName": "VideoJobs", "adminDigests": list(allowlist),
    }))
    policy.chmod(0o666)
    with pytest.raises(ValueError):
        load_admin_allowlist(policy)


def test_propose_and_approval_retries_are_idempotent(setup):
    table, _, kw, token = flow(setup)
    initial = datetime.now(timezone.utc)
    propose(table, kw, token, now=initial)
    approve(table, kw, token, now=initial + timedelta(minutes=1))
    propose(table, kw, token, now=initial + timedelta(minutes=2))
    approve(table, kw, token, now=initial + timedelta(minutes=3))
    assert len(table.records) == 2


def test_policy_revocation_blocks_enrollment(setup):
    table, _, kw, token = flow(setup)
    propose(table, kw, token)
    approve(table, kw, token)
    _, factory, allowed, digest = setup
    with pytest.raises(OnboardingBlocked, match="no longer authorized"):
        execute_enrollment(
            table, **{**kw, "allowed_admin_digests": frozenset([digest("admin-a")])},
            target_token=token("target"), operator_token=token("admin-a"),
        )
    assert len(table.records) == 2

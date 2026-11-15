"""No real Entra tokens, paid requests or Azure writes."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from cryptography.hazmat.primitives.asymmetric import rsa

from first_user_onboarding import (
    OnboardingBlocked, onboard_first_owner, project_id_for_enrollment,
)
from project_oidc import ProjectIdentityError, ProjectScopeDenied

TENANT = "773e9051-c546-4b75-9432-90f4f8242660"
APP = "a5a09b74-aead-455f-9de7-9b844c8a8465"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def uid():
    return str(uuid4())


class FakeTable:
    def __init__(self, *, fail_create=None):
        self.rows = {}
        self.calls = 0
        self.fail_create = fail_create

    def get_entity(self, *, partition_key, row_key):
        key = partition_key, row_key
        if key not in self.rows:
            raise ResourceNotFoundError("missing")
        return dict(self.rows[key])

    def create_entity(self, *, entity):
        self.calls += 1
        if self.fail_create == self.calls:
            raise RuntimeError("Storage unavailable")
        key = entity["PartitionKey"], entity["RowKey"]
        if key in self.rows:
            raise ResourceExistsError("conflict")
        self.rows[key] = dict(entity)


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signed(key, monkeypatch):
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL",
                       f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys")
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", f"api://{APP}")
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER, "aud": f"api://{APP}", "tid": TENANT,
        "sub": "real-subject-signed-fixture", "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=20)).timestamp()),
        "scp": "access_as_user",
    }
    from unittest.mock import MagicMock
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value = MagicMock(key=key.public_key())
    return payload, lambda x: jwt.encode(x, key, algorithm="RS256"), lambda url: jwks


def args(signed, **overrides):
    payload, encode, factory = signed
    enrollment = uid()
    result = {
        "token": encode(payload), "operator_id": uid(),
        "enrollment_id": enrollment, "project_id": project_id_for_enrollment(enrollment),
        "project_name": "B1 Test Project",
        "authorize_operator": lambda **values: True,
        "jwks_client_factory": factory,
    }
    result.update(overrides)
    return result


def test_success_records_user_project_membership_and_audit(signed):
    table = FakeTable()
    request = args(signed)
    result = onboard_first_owner(table, **request)
    assert result["status"] == "onboarded"
    assert len(table.rows) == 5
    assert table.rows[("projects", result["project_id"])]["ownerId"] == result["user_id"]
    assert table.rows[(
        f"project-members:{result['project_id']}", "member:" + result["user_id"],
    )]["role"] == "owner"
    assert len([r for r in table.rows.values() if r["PartitionKey"] == "project-oidc-users"]) == 1
    assert all("token" not in str(record).lower() for record in table.rows.values())


def test_exact_same_request_is_idempotent(signed):
    table = FakeTable()
    request = args(signed)
    first = onboard_first_owner(table, **request)
    again = onboard_first_owner(table, **request)
    assert first["user_id"] == again["user_id"]
    assert again["replayed"]
    assert len(table.rows) == 5


def test_admin_approval_missing_prevents_writes(signed):
    table = FakeTable()
    with pytest.raises(OnboardingBlocked, match="approval"):
        onboard_first_owner(table, **args(signed, authorize_operator=lambda **a: False))
    assert table.calls == 0


def test_admin_resolver_missing_prevents_writes(signed):
    table = FakeTable()
    with pytest.raises(OnboardingBlocked):
        onboard_first_owner(table, **args(signed, authorize_operator=None))
    assert table.calls == 0


def test_wrong_project_id_rejected_before_writes(signed):
    table = FakeTable()
    with pytest.raises(OnboardingBlocked):
        onboard_first_owner(table, **args(signed, project_id=uid()))
    assert table.calls == 0


def test_forged_token_rejected_before_approval_or_writes(signed):
    table = FakeTable()
    request = args(signed)
    segments = request["token"].split(".")
    request["token"] = segments[0] + "." + segments[1] + ".invalidsignature"
    with pytest.raises(ProjectIdentityError):
        onboard_first_owner(table, **request)
    assert table.calls == 0


def test_missing_scope_rejected_before_writes(signed):
    table = FakeTable()
    payload, encode, _ = signed
    with pytest.raises(ProjectScopeDenied):
        onboard_first_owner(table, **args(signed, token=encode({**payload, "scp": "profile"})))
    assert table.calls == 0


def test_wrong_tenant_rejected_before_writes(signed):
    table = FakeTable()
    payload, encode, _ = signed
    with pytest.raises(ProjectIdentityError):
        onboard_first_owner(table, **args(signed, token=encode({**payload, "tid": uid()})))
    assert table.calls == 0


def test_crash_before_mapping_then_safe_resume(signed):
    table = FakeTable(fail_create=3)
    request = args(signed)
    with pytest.raises(RuntimeError):
        onboard_first_owner(table, **request)
    assert not any(p == "project-oidc-users" for p, row in table.rows)
    table.fail_create = None
    result = onboard_first_owner(table, **request)
    assert result["status"] == "onboarded"
    assert len(table.rows) == 5


def test_existing_project_conflict_blocks_mapping(signed):
    table = FakeTable()
    request = args(signed)
    table.rows[("projects", request["project_id"])] = {
        "PartitionKey": "projects", "RowKey": request["project_id"],
        "name": "Hijacked", "ownerId": uid(), "status": "active", "schemaVersion": 1,
    }
    with pytest.raises(OnboardingBlocked, match="conflicts"):
        onboard_first_owner(table, **request)
    assert not any(p == "project-oidc-users" for p, row in table.rows)


def test_existing_mapping_with_other_enrollment_blocks(signed):
    table = FakeTable()
    initial = args(signed)
    onboard_first_owner(table, **initial)
    request = args(signed)
    with pytest.raises(OnboardingBlocked, match="already mapped"):
        onboard_first_owner(table, **request)
    assert len(table.rows) == 5


def test_revoked_mapping_never_reactivated_on_replay(signed):
    table = FakeTable()
    request = args(signed)
    onboard_first_owner(table, **request)
    for (p, r), value in table.rows.items():
        if p == "project-oidc-users":
            value["active"] = False
    with pytest.raises(OnboardingBlocked, match="already mapped"):
        onboard_first_owner(table, **request)


def test_second_operator_cannot_replay_original_approval(signed):
    table = FakeTable()
    request = args(signed)
    onboard_first_owner(table, **request)
    with pytest.raises(OnboardingBlocked, match="conflicts"):
        onboard_first_owner(table, **{**request, "operator_id": uid()})


def test_operator_approval_bound_to_subject_and_role(signed):
    table = FakeTable()
    checks = []
    def approve(**kwargs):
        checks.append(kwargs)
        return (
            kwargs["role"] == "owner"
            and kwargs["project_id"] == request["project_id"]
            and len(kwargs["subject_digest"]) == 64
        )
    request = args(signed, authorize_operator=approve)
    onboard_first_owner(table, **request)
    assert checks[0]["operator_id"] == request["operator_id"]

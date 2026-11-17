"""Local emulator regression tests for experimental F-03/F-04 controls."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from azure.data.tables import TableServiceClient
from finance_controls import PARTITION, FinanceDenied, reserve
from finance_recovery import (
    settle_unique_receipt, inspect_reservation,
    request_unsubmitted_release, approve_unsubmitted_release,
    receipt_key, provider_request_key,
)
from project_access import Actor

CONNECTION = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)
pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1",
    reason="Local isolated emulator only",
)


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def table():
    service = TableServiceClient.from_connection_string(CONNECTION)
    name = "Recover" + uuid.uuid4().hex[:17]
    client = service.create_table(name)
    try:
        yield client
    finally:
        service.delete_table(name)
        service.close()


def seed(table, project):
    for row in ("global:fal", "project:" + project):
        table.create_entity({"PartitionKey": PARTITION, "RowKey": row,
                             "ceilingCents": 10000, "spentCents": 0,
                             "reservedCents": 0, "schemaVersion": 1})


def row(table, key):
    return table.get_entity(partition_key=PARTITION, row_key=key)


def receipt_ok(job, request, receipt_id, cents, evidence):
    return (
        evidence.get("provider") == "fal"
        and evidence.get("job") == job
        and evidence.get("provider_request") == request
        and evidence.get("receipt_id") == receipt_id
        and evidence.get("cents") == cents
        and evidence.get("trusted") is True
    )


def evidence(job, request, receipt, cents):
    return {"provider": "fal", "job": job, "provider_request": request,
            "receipt_id": receipt, "cents": cents, "trusted": True}


def test_atomic_receipt_deduplication_across_jobs(table):
    project, first, second = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    request = "provider-test-1"
    receipt = "single-charge-record"
    settle_unique_receipt(
        table, job_id=first, actual_cents=60,
        provider_request_id=request, receipt_id=receipt,
        provider_receipt=evidence(first, request, receipt, 60),
        verify_provider_receipt=receipt_ok)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=second, actual_cents=60,
            provider_request_id=request, receipt_id=receipt,
            provider_receipt=evidence(second, request, receipt, 60),
            verify_provider_receipt=receipt_ok)
    assert row(table, "global:fal")["reservedCents"] == 100
    assert row(table, "global:fal")["spentCents"] == 60
    assert inspect_reservation(table, second)["state"] == "reserved"


def test_forged_or_missing_receipt_does_not_settle(table):
    project, job = uid(), uid()
    seed(table, project)
    reserve(table, project, job, 50)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=job, actual_cents=50,
            provider_request_id="request-1", receipt_id="r-1",
            provider_receipt=evidence(job, "request-OTHER", "r-1", 50),
            verify_provider_receipt=receipt_ok)
    assert row(table, "global:fal")["reservedCents"] == 50


def test_release_requires_distinct_admins_and_conclusive_evidence(table):
    project, job, first_id, second_id = uid(), uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job, 50)
    release_id = uid()
    first, second = Actor(first_id, True), Actor(second_id, True)
    lookup = lambda id: id in {first_id, second_id}
    request_unsubmitted_release(table, job_id=job, first_admin=first,
                                admin_lookup=lookup, release_id=release_id)
    with pytest.raises(FinanceDenied):
        approve_unsubmitted_release(
            table, release_id=release_id, second_admin=first,
            admin_lookup=lookup, no_submission_evidence={"submitted": False},
            verify_never_submitted=lambda j, e: True)
    with pytest.raises(FinanceDenied):
        approve_unsubmitted_release(
            table, release_id=release_id, second_admin=second,
            admin_lookup=lookup, no_submission_evidence={"state": "uncertain"},
            verify_never_submitted=lambda j, e: False)
    assert row(table, "global:fal")["reservedCents"] == 50
    result = approve_unsubmitted_release(
        table, release_id=release_id, second_admin=second,
        admin_lookup=lookup, no_submission_evidence={"state": "never_submitted"},
        verify_never_submitted=lambda j, e: e["state"] == "never_submitted")
    assert result["state"] == "released_unsubmitted"
    assert row(table, "global:fal")["reservedCents"] == 0
    assert row(table, "global:fal")["spentCents"] == 0
    assert inspect_reservation(table, job)["state"] == "released_unsubmitted"


def test_closed_reservation_cannot_be_released_again(table):
    project, job, first_id, second_id = uid(), uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job, 70)
    first, second = Actor(first_id, True), Actor(second_id, True)
    allow = lambda _: True
    release_id = uid()
    request_unsubmitted_release(table, job_id=job, first_admin=first,
                                admin_lookup=allow, release_id=release_id)
    data = {"never_submitted": True}
    verify = lambda j, e: e.get("never_submitted") is True
    approve_unsubmitted_release(table, release_id=release_id,
                                 second_admin=second, admin_lookup=allow,
                                 no_submission_evidence=data,
                                 verify_never_submitted=verify)
    with pytest.raises(FinanceDenied):
        approve_unsubmitted_release(table, release_id=release_id,
                                     second_admin=second, admin_lookup=allow,
                                     no_submission_evidence=data,
                                     verify_never_submitted=verify)
    assert row(table, "global:fal")["reservedCents"] == 0


def test_concurrent_receipt_replay_one_winner(table):
    project, job1, job2 = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job1, 100)
    reserve(table, project, job2, 100)
    receipt_id = "provider-unique-charge-id"
    jobs = [job1, job2]
    def submit(job):
        try:
            settle_unique_receipt(
                table, job_id=job, actual_cents=40,
                provider_request_id="provider-same-request",
                receipt_id=receipt_id,
                provider_receipt=evidence(job, "provider-same-request", receipt_id, 40),
                verify_provider_receipt=receipt_ok)
            return True
        except FinanceDenied:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, jobs))
    assert sum(results) == 1
    assert row(table, "global:fal")["spentCents"] == 40
    assert row(table, "global:fal")["reservedCents"] == 100


def test_same_job_cannot_start_two_release_requests(table):
    project, job, admin_id = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job, 20)
    actor = Actor(admin_id, True)
    lookup = lambda v: v == admin_id
    request_unsubmitted_release(table, job_id=job, first_admin=actor,
                                admin_lookup=lookup, release_id=uid())
    with pytest.raises(FinanceDenied):
        request_unsubmitted_release(table, job_id=job, first_admin=actor,
                                    admin_lookup=lookup, release_id=uid())
    assert row(table, "global:fal")["reservedCents"] == 20


def test_revoked_first_admin_blocks_release(table):
    project, job, first_id, second_id = uid(), uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job, 30)
    release_id = uid()
    first, second = Actor(first_id, True), Actor(second_id, True)
    request_unsubmitted_release(table, job_id=job, first_admin=first,
                                admin_lookup=lambda v: v == first_id,
                                release_id=release_id)
    with pytest.raises(FinanceDenied, match="revoked"):
        approve_unsubmitted_release(
            table, release_id=release_id, second_admin=second,
            admin_lookup=lambda v: v == second_id,
            no_submission_evidence={"never_submitted": True},
            verify_never_submitted=lambda j, e: True,
        )
    assert row(table, "global:fal")["reservedCents"] == 30
    assert row(table, "release:" + release_id)["status"] == "pending"


def test_ambiguous_non_submission_must_never_release(table):
    project, job, first_id, second_id = uid(), uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, job, 70)
    approval_id = uid()
    request_unsubmitted_release(
        table, job_id=job, first_admin=Actor(first_id, True),
        admin_lookup=lambda v: v in {first_id, second_id},
        release_id=approval_id,
    )
    with pytest.raises(FinanceDenied):
        approve_unsubmitted_release(
            table, release_id=approval_id,
            second_admin=Actor(second_id, True),
            admin_lookup=lambda v: v in {first_id, second_id},
            no_submission_evidence={"state": "unknown"},
            verify_never_submitted=lambda job, evidence: False,
        )
    assert row(table, "global:fal")["reservedCents"] == 70


def test_same_provider_request_different_receipts_cannot_charge_two_jobs(table):
    project, first, second = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    request_id = "same-request-two-receipts"
    settle_unique_receipt(
        table, job_id=first, actual_cents=70,
        provider_request_id=request_id, receipt_id="receipt-A",
        provider_receipt=evidence(first, request_id, "receipt-A", 70),
        verify_provider_receipt=receipt_ok)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=second, actual_cents=80,
            provider_request_id=request_id, receipt_id="receipt-B",
            provider_receipt=evidence(second, request_id, "receipt-B", 80),
            verify_provider_receipt=receipt_ok)
    assert row(table, "global:fal")["spentCents"] == 70
    assert row(table, "global:fal")["reservedCents"] == 100
    assert row(table, "project:" + project)["spentCents"] == 70
    assert inspect_reservation(table, second)["state"] == "reserved"
    assert row(table, provider_request_key("fal", request_id))["jobId"] == first



@pytest.mark.parametrize("variant", ["REQUEST-CASE-1", "Request-Case-1", "ＲＥＱＵＥＳＴ-ＣＡＳＥ-１"])
def test_provider_request_case_variant_cannot_settle_second_job(table, variant):
    project, first, second = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    original = "request-case-1"
    settle_unique_receipt(
        table, job_id=first, actual_cents=60,
        provider_request_id=original, receipt_id="case-receipt-first",
        provider_receipt=evidence(first, original, "case-receipt-first", 60),
        verify_provider_receipt=receipt_ok)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=second, actual_cents=60,
            provider_request_id=variant, receipt_id="case-receipt-second",
            provider_receipt=evidence(second, variant, "case-receipt-second", 60),
            verify_provider_receipt=receipt_ok)
    assert row(table, "global:fal")["spentCents"] == 60
    assert row(table, "global:fal")["reservedCents"] == 100
    assert inspect_reservation(table, second)["state"] == "reserved"
    assert row(table, provider_request_key("fal", variant))["jobId"] == first

def test_concurrent_same_request_distinct_receipts_one_winner(table):
    project, first, second = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    def submit(job_and_receipt):
        job, receipt = job_and_receipt
        try:
            settle_unique_receipt(
                table, job_id=job, actual_cents=40,
                provider_request_id="same-request-race", receipt_id=receipt,
                provider_receipt=evidence(job, "same-request-race", receipt, 40),
                verify_provider_receipt=receipt_ok)
            return True
        except FinanceDenied:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [(first, "race-a"), (second, "race-b")]))
    assert sum(results) == 1
    assert row(table, "global:fal")["spentCents"] == 40
    assert row(table, "global:fal")["reservedCents"] == 100
    assert row(table, "project:" + project)["spentCents"] == 40
    assert sum(inspect_reservation(table, job)["state"] == "settled"
               for job in (first, second)) == 1


@pytest.mark.parametrize("second_receipt", ["receipt-a", "RECEIPT-A"])
def test_case_variant_receipt_cannot_settle_other_job(table, second_receipt):
    project, first, second = uid(), uid(), uid()
    seed(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    settle_unique_receipt(
        table, job_id=first, actual_cents=35,
        provider_request_id="request-first", receipt_id="Receipt-A",
        provider_receipt=evidence(first, "request-first", "Receipt-A", 35),
        verify_provider_receipt=receipt_ok)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=second, actual_cents=40,
            provider_request_id="request-second", receipt_id=second_receipt,
            provider_receipt=evidence(second, "request-second", second_receipt, 40),
            verify_provider_receipt=receipt_ok)
    assert inspect_reservation(table, second)["state"] == "reserved"
    assert row(table, "global:fal")["spentCents"] == 35
    assert row(table, "global:fal")["reservedCents"] == 100


@pytest.mark.parametrize("invalid_receipt", ["Receipt-A ", " Receipt-A",
                                             "Receipt-A\u200b", "Receipt-A\x00"])
def test_noncanonical_receipts_rejected_without_budget_movement(table, invalid_receipt):
    project, job = uid(), uid()
    seed(table, project)
    reserve(table, project, job, 60)
    with pytest.raises(FinanceDenied):
        settle_unique_receipt(
            table, job_id=job, actual_cents=25,
            provider_request_id="request-one", receipt_id=invalid_receipt,
            provider_receipt=evidence(job, "request-one", invalid_receipt, 25),
            verify_provider_receipt=receipt_ok)
    assert row(table, "global:fal")["spentCents"] == 0
    assert row(table, "global:fal")["reservedCents"] == 60
    assert inspect_reservation(table, job)["state"] == "reserved"


def test_signed_two_admin_budget_approval_and_revocation(table, monkeypatch):
    """Enforce real signed caller identities and atomic two-admin approval."""
    import hashlib
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from finance_authorization import approve_increase_verified

    tenant = "773e9051-c546-4b75-9432-90f4f8242660"
    app = "a5a09b74-aead-455f-9de7-9b844c8a8465"
    issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
    monkeypatch.setenv("PROJECT_OIDC_ISSUER", issuer)
    monkeypatch.setenv("PROJECT_OIDC_AUDIENCE", f"api://{app}")
    monkeypatch.setenv("PROJECT_OIDC_JWKS_URL",
                       f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = MagicMock(key=key.public_key())
    def factory(_):
        return client
    def token(subject):
        current = datetime.now(timezone.utc)
        return jwt.encode({
            "iss": issuer, "aud": f"api://{app}", "tid": tenant,
            "sub": subject, "iat": int(current.timestamp()),
            "exp": int((current + timedelta(minutes=30)).timestamp()),
            "scp": "access_as_user",
        }, key, algorithm="RS256")
    ids = {subject: uid() for subject in ("requester", "first", "second")}
    digests = {subject: hashlib.sha256(
        (issuer + "|" + subject).encode("utf-8")).hexdigest()
        for subject in ids}
    for subject in ids:
        table.create_entity({"PartitionKey": "project-oidc-users",
                             "RowKey": digests[subject], "active": True,
                             "userId": ids[subject]})
    allowed = frozenset(digests.values())
    project = uid()
    seed(table, project)

    def new_request():
        request_id = uid()
        table.create_entity({
            "PartitionKey": PARTITION, "RowKey": "increase:" + request_id,
            "scope": "global:fal", "requesterId": ids["requester"],
            "proposedCents": 11000, "status": "pending",
            "firstApproverId": "", "secondApproverId": "",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        return request_id

    request_id = new_request()
    assert approve_increase_verified(
        table, request_id=request_id, access_token=token("first"),
        allowed_admin_digests=allowed,
        jwks_client_factory=factory) == "awaiting_second_approval"
    with pytest.raises(FinanceDenied):
        approve_increase_verified(
            table, request_id=request_id, access_token=token("first"),
            allowed_admin_digests=allowed, jwks_client_factory=factory)
    assert approve_increase_verified(
        table, request_id=request_id, access_token=token("second"),
        allowed_admin_digests=allowed, jwks_client_factory=factory) == "approved"
    assert row(table, "global:fal")["ceilingCents"] == 11000
    with pytest.raises(FinanceDenied):
        approve_increase_verified(
            table, request_id=request_id, access_token=token("second"),
            allowed_admin_digests=allowed, jwks_client_factory=factory)

    revoked_request = new_request()
    assert approve_increase_verified(
        table, request_id=revoked_request, access_token=token("first"),
        allowed_admin_digests=allowed,
        jwks_client_factory=factory) == "awaiting_second_approval"
    mapping = table.get_entity(partition_key="project-oidc-users",
                               row_key=digests["first"])
    mapping["active"] = False
    table.update_entity(mapping)
    with pytest.raises(FinanceDenied, match="revoked"):
        approve_increase_verified(
            table, request_id=revoked_request, access_token=token("second"),
            allowed_admin_digests=allowed, jwks_client_factory=factory)
    assert row(table, "increase:" + revoked_request)["status"] == "pending"
    assert row(table, "global:fal")["ceilingCents"] == 11000

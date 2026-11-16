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

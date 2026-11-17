"""L-2 (Issue #62): settlement binds the provider request ID to the job's
durable submission record before the verifier runs and before any finance
mutation. Azurite Table emulator only; no provider or network calls.

The verifiers below are deliberately permissive ("mis-implemented") so the
tests prove the ledger itself enforces the binding.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from azure.data.tables import TableServiceClient

from budgeted_fal_execution import settle_verified_charge
from fal_billing_reconciliation import reconcile_verified_fal_charge
from finance_controls import PARTITION, FinanceDenied, reserve, scope_key
from finance_recovery import (
    inspect_reservation, provider_request_key, receipt_key,
    settle_unique_receipt,
)
from provider_submission_ledger import (
    claim_intent, create_intent, read_intent, record_provider_request_id,
)

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


def _table(prefix):
    service = TableServiceClient.from_connection_string(CONNECTION)
    name = prefix + uuid.uuid4().hex[:16]
    return service, name, service.create_table(name)


@pytest.fixture
def finance():
    service, name, client = _table("L2Finance")
    try:
        yield client
    finally:
        service.delete_table(name)
        service.close()


@pytest.fixture
def subs():
    service, name, client = _table("L2Intents")
    try:
        yield client
    finally:
        service.delete_table(name)
        service.close()


def seed(table, project, ceiling=10000):
    for key in ("global:fal", scope_key(project)):
        table.create_entity({"PartitionKey": PARTITION, "RowKey": key,
                             "ceilingCents": ceiling, "spentCents": 0,
                             "reservedCents": 0, "schemaVersion": 1})


def submitted(subs, job, request_id):
    create_intent(subs, job)
    claim_intent(subs, job)
    record_provider_request_id(subs, job, request_id)


def row(table, key):
    return table.get_entity(partition_key=PARTITION, row_key=key)


def absent(table, key):
    with pytest.raises(ResourceNotFoundError):
        table.get_entity(partition_key=PARTITION, row_key=key)


def snapshot(table, project, *jobs):
    """Every balance and reservation that settlement could move."""
    state = {k: (row(table, k)["reservedCents"], row(table, k)["spentCents"])
             for k in ("global:fal", scope_key(project))}
    for job in jobs:
        r = row(table, "reservation:" + job)
        state[job] = (r["state"], r["reservedCents"], r.get("actualCents"),
                      r.get("providerRequestId"), r.get("receiptKey"))
    return state


def audit_rows(table):
    return [e for e in table.query_entities(
        "PartitionKey eq @p", parameters={"p": PARTITION})
        if e["RowKey"].startswith("audit:")]


class Recorder:
    """Permissive verifier that records whether it was consulted."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return True


def settle(finance, subs, job, request_id, receipt_id, cents, verifier):
    return settle_unique_receipt(
        finance, submission_table=subs, job_id=job, actual_cents=cents,
        provider_request_id=request_id, receipt_id=receipt_id,
        provider_receipt={"simulated": True, "receipt_id": receipt_id},
        verify_provider_receipt=verifier)


@pytest.fixture
def two_jobs(finance, subs):
    project, job_a, job_b = uid(), uid(), uid()
    seed(finance, project)
    reserve(finance, project, job_a, 100)
    reserve(finance, project, job_b, 100)
    submitted(subs, job_a, "req-A")
    submitted(subs, job_b, "req-B")
    return project, job_a, job_b


def test_job_b_cannot_claim_job_a_request_first_and_no_money_moves(
        finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    before = snapshot(finance, project, job_a, job_b)
    audits_before = len(audit_rows(finance))
    verifier = Recorder()

    with pytest.raises(FinanceDenied, match="durable submission"):
        settle(finance, subs, job_b, "req-A", "receipt-b", 60, verifier)

    assert verifier.calls == []          # rejected before the verifier
    assert snapshot(finance, project, job_a, job_b) == before
    assert len(audit_rows(finance)) == audits_before
    absent(finance, provider_request_key("fal", "req-A"))
    absent(finance, receipt_key("fal", "receipt-b"))
    assert read_intent(subs, job_a)["providerRequestId"] == "req-A"

    # Job A's request was not consumed by B's attempt: A still settles.
    result = settle(finance, subs, job_a, "req-A", "receipt-a", 40, Recorder())
    assert result["state"] == "settled"
    assert row(finance, provider_request_key("fal", "req-A"))["jobId"] == job_a
    assert row(finance, "global:fal")["spentCents"] == 40
    assert row(finance, "global:fal")["reservedCents"] == 100
    assert inspect_reservation(finance, job_b)["state"] == "reserved"


def test_job_b_cannot_reuse_job_a_request_after_a_settled(finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    settle(finance, subs, job_a, "req-A", "receipt-a", 40, Recorder())
    before = snapshot(finance, project, job_a, job_b)
    verifier = Recorder()
    with pytest.raises(FinanceDenied, match="durable submission"):
        settle(finance, subs, job_b, "req-A", "receipt-b2", 40, verifier)
    assert verifier.calls == []
    assert snapshot(finance, project, job_a, job_b) == before


@pytest.mark.parametrize("presented", ["REQ-B", "req-b", "req-B ", "req-B" + chr(0x200B),
                                       "req-C", "req-"])
def test_variant_of_own_durable_request_is_rejected_exactly(
        finance, subs, two_jobs, presented):
    project, job_a, job_b = two_jobs
    before = snapshot(finance, project, job_a, job_b)
    with pytest.raises(FinanceDenied):
        settle(finance, subs, job_b, presented, "receipt-variant", 10, Recorder())
    assert snapshot(finance, project, job_a, job_b) == before


@pytest.mark.parametrize("stage", ["missing", "prepared", "submitting"])
def test_unresolved_or_missing_submission_never_settles(finance, subs, stage):
    project, job = uid(), uid()
    seed(finance, project)
    reserve(finance, project, job, 50)
    if stage != "missing":
        create_intent(subs, job)
    if stage == "submitting":
        claim_intent(subs, job)
    before = snapshot(finance, project, job)
    verifier = Recorder()
    with pytest.raises(FinanceDenied):
        settle(finance, subs, job, "req-unresolved", "receipt-u", 50, verifier)
    assert verifier.calls == []
    assert snapshot(finance, project, job) == before
    absent(finance, provider_request_key("fal", "req-unresolved"))


def test_missing_submission_ledger_fails_closed(finance):
    project, job = uid(), uid()
    seed(finance, project)
    reserve(finance, project, job, 50)
    before = snapshot(finance, project, job)
    for kwargs in ({}, {"submission_table": None}):
        with pytest.raises(FinanceDenied, match="ledger required"):
            settle_unique_receipt(
                finance, job_id=job, actual_cents=10,
                provider_request_id="req-x", receipt_id="receipt-x",
                provider_receipt={"simulated": True},
                verify_provider_receipt=Recorder(), **kwargs)
    assert snapshot(finance, project, job) == before


def test_unavailable_submission_ledger_fails_closed(finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    before = snapshot(finance, project, job_a, job_b)
    with patch("finance_recovery.read_intent",
               side_effect=ServiceRequestError("ledger unreachable")):
        with pytest.raises(FinanceDenied, match="unavailable"):
            settle(finance, subs, job_b, "req-B", "receipt-b", 10, Recorder())
    assert snapshot(finance, project, job_a, job_b) == before


def test_misrouted_ledger_returning_other_jobs_record_is_rejected(
        finance, subs, two_jobs):
    """A ledger lookup that returns job A's record for job B fails closed."""
    project, job_a, job_b = two_jobs
    record_a = read_intent(subs, job_a)
    before = snapshot(finance, project, job_a, job_b)
    with patch("finance_recovery.read_intent", return_value=record_a):
        with pytest.raises(FinanceDenied, match="does not belong"):
            settle(finance, subs, job_b, "req-A", "receipt-b", 10, Recorder())
    assert snapshot(finance, project, job_a, job_b) == before


def test_legitimate_settlement_is_idempotent_and_single_charge(
        finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    settle(finance, subs, job_a, "req-A", "receipt-a", 45, Recorder())
    after_first = snapshot(finance, project, job_a, job_b)
    for receipt in ("receipt-a", "receipt-a-retry"):
        with pytest.raises(FinanceDenied):
            settle(finance, subs, job_a, "req-A", receipt, 45, Recorder())
    assert snapshot(finance, project, job_a, job_b) == after_first
    assert row(finance, "global:fal")["spentCents"] == 45
    assert row(finance, scope_key(project))["spentCents"] == 45


def test_concurrent_cross_job_claim_only_rightful_job_settles(
        finance, subs, two_jobs):
    project, job_a, job_b = two_jobs

    def attempt(job_receipt):
        job, receipt = job_receipt
        try:
            settle(finance, subs, job, "req-A", receipt, 30, Recorder())
            return job
        except FinanceDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [w for w in pool.map(attempt, [(job_b, "race-b"),
                                                 (job_a, "race-a")]) if w]
    assert winners == [job_a]
    assert row(finance, "global:fal")["spentCents"] == 30
    assert row(finance, "global:fal")["reservedCents"] == 100
    assert inspect_reservation(finance, job_b)["state"] == "reserved"


def test_bridge_settle_verified_charge_enforces_binding(finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    before = snapshot(finance, project, job_a, job_b)
    with pytest.raises(FinanceDenied, match="durable submission"):
        settle_verified_charge(
            finance_table=finance, submission_table=subs, job_id=job_b,
            charge_cents=20, provider_request_id="req-A",
            provider_receipt={"receipt_id": "bridge-b"},
            verify_provider_receipt=lambda *args: True)
    with pytest.raises(FinanceDenied, match="ledger required"):
        settle_verified_charge(
            finance_table=finance, job_id=job_b,
            charge_cents=20, provider_request_id="req-B",
            provider_receipt={"receipt_id": "bridge-b"},
            verify_provider_receipt=lambda *args: True)
    assert snapshot(finance, project, job_a, job_b) == before


def test_reconciliation_uses_real_separate_submission_ledger(finance, subs, two_jobs):
    project, job_a, job_b = two_jobs
    result = reconcile_verified_fal_charge(
        finance, subs, job_b, actual_cents=25,
        provider_receipt={"receipt_id": "recon-b"},
        verify_provider_receipt=lambda job, request, cents, receipt:
            job == job_b and request == "req-B" and cents == 25)
    assert result["state"] == "settled"
    assert row(finance, "reservation:" + job_b)["providerRequestId"] == "req-B"
    assert row(finance, provider_request_key("fal", "req-B"))["jobId"] == job_b
    assert inspect_reservation(finance, job_a)["state"] == "reserved"
    assert row(finance, "global:fal")["spentCents"] == 25

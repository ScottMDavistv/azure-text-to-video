"""Azure Tables SDK finance security tests against local Azurite only.

Opt-in CI, isolated table; no production Azure account or FAL requests.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from azure.core.exceptions import HttpResponseError, ServiceResponseError
from azure.data.tables import TableServiceClient

from finance_controls import (
    FinanceDenied, AmbiguousCommit, reserve, scope_key, settle, PARTITION,
)

AZURITE_CONNECTION = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1",
    reason="Requires explicit local Azurite Table emulator opt-in",
)


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def table():
    service = TableServiceClient.from_connection_string(AZURITE_CONNECTION)
    name = "FinanceTest" + uuid.uuid4().hex[:16]
    client = service.create_table(name)
    try:
        yield client
    finally:
        service.delete_table(name)
        service.close()


def seed(table, project_id, limit):
    for key in ("global:fal", scope_key(project_id)):
        table.create_entity({
            "PartitionKey": PARTITION, "RowKey": key,
            "ceilingCents": limit, "spentCents": 0,
            "reservedCents": 0, "schemaVersion": 1,
        })


def entity(table, row):
    return table.get_entity(partition_key=PARTITION, row_key=row)


def verified(job_id, actual, receipt):
    return (
        receipt.get("job_id") == job_id
        and receipt.get("amount_cents") == actual
        and receipt.get("verified") is True
    )


def test_azurite_concurrent_reservations_and_limit_invariant(table):
    project = uid()
    seed(table, project, 100)
    jobs = [uid() for _ in range(8)]

    def submit(job_id):
        try:
            reserve(table, project, job_id, 40)
            return True
        except FinanceDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(submit, jobs))
    successful = sum(outcomes)
    assert successful == 2
    for key in ("global:fal", scope_key(project)):
        limit = entity(table, key)
        assert limit["reservedCents"] == successful * 40
        assert limit["spentCents"] == 0
        assert limit["reservedCents"] <= limit["ceilingCents"]


def test_azurite_duplicate_job_reservation_is_atomic(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    reserve(table, project, job, 40)
    with pytest.raises(FinanceDenied):
        reserve(table, project, job, 40)
    for key in ("global:fal", scope_key(project)):
        assert entity(table, key)["reservedCents"] == 40


def test_azurite_settlement_requires_verified_evidence(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    reserve(table, project, job, 60)
    with pytest.raises(FinanceDenied):
        settle(table, job, 45)
    assert entity(table, "reservation:" + job)["state"] == "reserved"
    receipt = {"job_id": job, "amount_cents": 45, "verified": True}
    settle(table, job, 45, provider_receipt=receipt,
           verify_provider_receipt=verified)
    assert entity(table, "reservation:" + job)["state"] == "settled"
    for key in ("global:fal", scope_key(project)):
        limit = entity(table, key)
        assert limit["reservedCents"] == 0
        assert limit["spentCents"] == 45


def test_azurite_double_settlement_never_double_charges(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    reserve(table, project, job, 50)
    receipt = {"job_id": job, "amount_cents": 38, "verified": True}
    def attempt(_):
        try:
            settle(table, job, 38, provider_receipt=receipt,
                   verify_provider_receipt=verified)
            return True
        except FinanceDenied:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(results) == 1
    assert entity(table, scope_key(project))["spentCents"] == 38
    assert entity(table, "global:fal")["reservedCents"] == 0


def test_azurite_receipt_mismatch_refuses_settlement(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    reserve(table, project, job, 50)
    wrong = {"job_id": uid(), "amount_cents": 10, "verified": True}
    with pytest.raises(FinanceDenied):
        settle(table, job, 10, provider_receipt=wrong,
               verify_provider_receipt=verified)
    assert entity(table, "reservation:" + job)["state"] == "reserved"


class LostResponse:
    """Simulate successful Azure commit followed by lost client response."""
    def __init__(self, table):
        self.table = table
    def get_entity(self, **kwargs):
        return self.table.get_entity(**kwargs)
    def submit_transaction(self, actions, **kwargs):
        self.table.submit_transaction(actions, **kwargs)
        raise ServiceResponseError(message="commit succeeded but response lost")


def test_azurite_lost_reservation_response_requires_reconciliation(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    with pytest.raises(AmbiguousCommit, match="reconcile before retry"):
        reserve(LostResponse(table), project, job, 30)
    assert entity(table, "reservation:" + job)["state"] == "reserved"
    with pytest.raises(FinanceDenied):
        reserve(table, project, job, 30)
    assert entity(table, "global:fal")["reservedCents"] == 30


def test_azurite_lost_settlement_response_requires_reconciliation(table):
    project, job = uid(), uid()
    seed(table, project, 100)
    reserve(table, project, job, 45)
    receipt = {"job_id": job, "amount_cents": 35, "verified": True}
    with pytest.raises(FinanceDenied, match="reconcile before retry"):
        settle(LostResponse(table), job, 35, provider_receipt=receipt,
               verify_provider_receipt=verified)
    assert entity(table, "reservation:" + job)["state"] == "settled"
    with pytest.raises(FinanceDenied):
        settle(table, job, 35, provider_receipt=receipt,
               verify_provider_receipt=verified)
    assert entity(table, "global:fal")["spentCents"] == 35


def test_azurite_all_jobs_succeed_under_contention_with_capacity(table):
    project = uid()
    n = 24
    seed(table, project, n * 100)
    jobs = [uid() for _ in range(n)]

    def submit(job):
        reserve(table, project, job, 100)
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(submit, jobs))

    assert all(outcomes)
    assert entity(table, "global:fal")["reservedCents"] == n * 100
    assert entity(table, scope_key(project))["reservedCents"] == n * 100
    assert len([e for e in table.query_entities(
        "PartitionKey eq 'render-finance-v1' and RowKey ge 'reservation:' and RowKey lt 'reservation;'"
    )]) == n


def test_azurite_money_ceiling_is_sdk_compatible(table):
    from finance_controls import check_money, MAX_CENTS
    assert MAX_CENTS == 2**31 - 1
    project = uid()
    seed(table, project, MAX_CENTS)
    reserve(table, project, uid(), MAX_CENTS)
    assert entity(table, "global:fal")["reservedCents"] == MAX_CENTS
    with pytest.raises(FinanceDenied):
        check_money(MAX_CENTS + 1)

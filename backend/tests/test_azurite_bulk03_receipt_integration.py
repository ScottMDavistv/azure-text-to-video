"""Real ledger integration for Bulk03 F-14 and PR5 F-03, Azurite only.

These simulated "trusted" receipts are NOT an authenticated FAL source.
"""
import os
from uuid import uuid4
from unittest.mock import patch

import pytest
from azure.data.tables import TableServiceClient

from finance_controls import FinanceDenied, PARTITION, reserve, scope_key
from fal_billing_reconciliation import reconcile_verified_fal_charge
from budgeted_fal_execution import ExecutionBlocked

CONNECTION = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)
pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1",
    reason="Opt-in local Azurite emulator only",
)


@pytest.fixture
def table():
    service = TableServiceClient.from_connection_string(CONNECTION)
    name = "F14Integration" + uuid4().hex[:13]
    client = service.create_table(name)
    try:
        yield client
    finally:
        service.delete_table(name)
        service.close()


def setup_budget(table, project):
    for key in ("global:fal", scope_key(project)):
        table.create_entity({
            "PartitionKey": PARTITION, "RowKey": key,
            "ceilingCents": 1000, "reservedCents": 0,
            "spentCents": 0, "schemaVersion": 1,
        })


def read(table, key):
    return table.get_entity(partition_key=PARTITION, row_key=key)


def valid_receipt(job, request, amount, receipt):
    return (receipt.get("job_id") == job
            and receipt.get("provider_request_id") == request
            and receipt.get("actual_cents") == amount
            and receipt.get("receipt_id") is not None
            and receipt.get("verified") is True)


def receipt(job, request, cents, receipt_id):
    return {"job_id": job, "provider_request_id": request,
            "actual_cents": cents, "receipt_id": receipt_id, "verified": True}


def reconcile(table, job, request, amount, proof, verifier=valid_receipt):
    with patch("fal_billing_reconciliation.read_intent",
               return_value={"state": "submitted", "providerRequestId": request}):
        return reconcile_verified_fal_charge(
            table, None, job,
            actual_cents=amount, provider_receipt=proof,
            verify_provider_receipt=verifier,
        )


def test_real_atomic_verified_charge_settlement(table):
    project, job = str(uuid4()), str(uuid4())
    setup_budget(table, project)
    reserve(table, project, job, 100)
    result = reconcile(table, job, "provider-req", 45,
                       receipt(job, "provider-req", 45, "receipt-a"))
    assert result["state"] == "settled"
    assert read(table, "global:fal")["spentCents"] == 45
    assert read(table, "global:fal")["reservedCents"] == 0
    assert read(table, "reservation:" + job)["receiptKey"].startswith("receipt:")
    assert read(table, scope_key(project))["spentCents"] == 45


def test_real_ledger_rejects_cross_job_duplicate_receipt(table):
    project, first, second = str(uuid4()), str(uuid4()), str(uuid4())
    setup_budget(table, project)
    reserve(table, project, first, 100)
    reserve(table, project, second, 100)
    reconcile(table, first, "request-a", 40,
              receipt(first, "request-a", 40, "duplicate-id"))
    with pytest.raises(FinanceDenied):
        reconcile(table, second, "request-b", 40,
                  receipt(second, "request-b", 40, "duplicate-id"))
    assert read(table, "global:fal")["spentCents"] == 40
    assert read(table, "global:fal")["reservedCents"] == 100


def test_real_ledger_rejects_forged_request(table):
    project, job = str(uuid4()), str(uuid4())
    setup_budget(table, project)
    reserve(table, project, job, 90)
    with pytest.raises(Exception):
        reconcile(table, job, "request-real", 55,
                  receipt(job, "request-fake", 55, "receipt-b"))
    assert read(table, "global:fal")["reservedCents"] == 90
    assert read(table, "global:fal")["spentCents"] == 0


def test_real_ledger_requires_receipt_id(table):
    project, job = str(uuid4()), str(uuid4())
    setup_budget(table, project)
    reserve(table, project, job, 40)
    with pytest.raises(ExecutionBlocked):
        reconcile(table, job, "request", 20,
                  {"job_id": job, "provider_request_id": "request",
                   "actual_cents": 20, "verified": True})
    assert read(table, "reservation:" + job)["state"] == "reserved"


def test_no_zero_charge_without_positive_no_charge_proof(table):
    project, job = str(uuid4()), str(uuid4())
    setup_budget(table, project)
    reserve(table, project, job, 40)
    with pytest.raises(Exception):
        reconcile(table, job, "request", 0,
                  receipt(job, "request", 0, "zero-receipt"),
                  verifier=lambda *args: False)
    assert read(table, "global:fal")["reservedCents"] == 40

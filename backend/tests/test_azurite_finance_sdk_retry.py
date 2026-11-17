"""Claude NF-01 regression: SDK HTTP fault injection against isolated Azurite.

The batch commits server-side and the transport returns a false HTTP failure.
SDK retries must be disabled at the finance write call site. No paid calls.
Adapted from the attached Claude test_sdk_retry_fault_injection.py.
"""
import json
import os
import uuid

import pytest
import requests
from azure.core.exceptions import ServiceResponseError
from azure.core.pipeline.transport import RequestsTransport, RequestsTransportResponse
from azure.data.tables import TableClient, TableServiceClient

from finance_controls import (
    AmbiguousCommit, FinanceConflict, FinanceDenied,
    PARTITION, reserve, scope_key,
)
from finance_recovery import settle_unique_receipt
from provider_submission_ledger import (
    claim_intent, create_intent, record_provider_request_id,
)

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1",
    reason="Requires local isolated Azurite opt-in",
)
CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


class FaultTransport(RequestsTransport):
    """Commit the batch, then inject one false HTTP error at SDK transport."""
    def __init__(self, status=None, lose_response=False):
        super().__init__()
        self.status = status
        self.lose_response = lose_response
        self.fired = False
        self.batch_sends = 0

    def send(self, request, **kw):
        if request.method == "POST" and "$batch" in request.url:
            self.batch_sends += 1
            if not self.fired:
                self.fired = True
                RequestsTransport.send(self, request, **kw)
                if self.lose_response:
                    raise ServiceResponseError("connection reset after send")
                r = requests.Response()
                r.status_code, r.reason, r.url = (
                    self.status, "injected", request.url
                )
                r._content = json.dumps({
                    "odata.error": {
                        "code": "X",
                        "message": {"lang": "en", "value": "x"},
                    },
                }).encode()
                r.headers["content-type"] = "application/json;odata=minimalmetadata"
                return RequestsTransportResponse(request, r)
        return super().send(request, **kw)


@pytest.fixture
def tables():
    svc = TableServiceClient.from_connection_string(CONN)
    name = "Fault" + uuid.uuid4().hex[:16]
    svc.create_table(name)
    plain = TableClient.from_connection_string(CONN, table_name=name)
    try:
        yield name, plain
    finally:
        svc.delete_table(name)
        svc.close()


def submitted(subs, job, request_id):
    """Durable submitted intent via the real ledger API (L-2 binding)."""
    create_intent(subs, job)
    claim_intent(subs, job)
    record_provider_request_id(subs, job, request_id)


def seed(table, project):
    for key in ("global:fal", scope_key(project)):
        table.create_entity({
            "PartitionKey": PARTITION, "RowKey": key,
            "ceilingCents": 1000, "spentCents": 0,
            "reservedCents": 0, "schemaVersion": 1,
        })


@pytest.mark.parametrize("status", [500, 503, 504])
def test_committed_reservation_not_retried_by_azure_sdk(tables, status):
    name, plain = tables
    project, job = str(uuid.uuid4()), str(uuid.uuid4())
    seed(plain, project)
    transport = FaultTransport(status=status)
    faulty = TableClient.from_connection_string(
        CONN, table_name=name, transport=transport,
        retry_backoff_factor=0.01,
    )
    try:
        reserve(faulty, project, job, 30)
    except AmbiguousCommit:
        pass
    except (FinanceDenied, FinanceConflict) as exc:
        pytest.fail("Committed write misclassified: " + type(exc).__name__)
    assert transport.batch_sends == 1
    assert plain.get_entity(
        partition_key=PARTITION, row_key="global:fal"
    )["reservedCents"] == 30
    assert plain.get_entity(
        partition_key=PARTITION, row_key="reservation:" + job
    )["state"] == "reserved"


def test_committed_settlement_not_retried_by_azure_sdk(tables):
    name, plain = tables
    project, job = str(uuid.uuid4()), str(uuid.uuid4())
    seed(plain, project)
    reserve(plain, project, job, 50)
    # Durable intent lives in its own partition, read via the fault-free client.
    submitted(plain, job, "request-" + job)
    receipt = {"job_id": job, "amount_cents": 35, "receipt_id": "settle-" + job}
    ok = lambda j, req, rid, a, r: (
        r["job_id"] == j and r["amount_cents"] == a
        and req == "request-" + job and rid == r["receipt_id"]
    )
    transport = FaultTransport(status=503)
    faulty = TableClient.from_connection_string(
        CONN, table_name=name, transport=transport,
        retry_backoff_factor=0.01,
    )
    try:
        settle_unique_receipt(
            faulty, submission_table=plain, job_id=job, actual_cents=35,
            provider_request_id="request-" + job,
            receipt_id=receipt["receipt_id"],
            provider_receipt=receipt, verify_provider_receipt=ok,
        )
    except AmbiguousCommit:
        pass
    except (FinanceDenied, FinanceConflict) as exc:
        pytest.fail("Committed settlement misclassified: " + type(exc).__name__)
    assert transport.batch_sends == 1
    assert plain.get_entity(
        partition_key=PARTITION, row_key="reservation:" + job
    )["state"] == "settled"
    assert plain.get_entity(
        partition_key=PARTITION, row_key="global:fal"
    )["spentCents"] == 35


def test_connection_lost_after_applied_reservation_is_ambiguous(tables):
    name, plain = tables
    project, job = str(uuid.uuid4()), str(uuid.uuid4())
    seed(plain, project)
    transport = FaultTransport(lose_response=True)
    faulty = TableClient.from_connection_string(
        CONN, table_name=name, transport=transport,
    )
    with pytest.raises(AmbiguousCommit):
        reserve(faulty, project, job, 30)
    assert transport.batch_sends == 1
    assert plain.get_entity(
        partition_key=PARTITION, row_key="global:fal"
    )["reservedCents"] == 30

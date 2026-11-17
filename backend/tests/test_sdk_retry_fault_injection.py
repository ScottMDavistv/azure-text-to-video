"""SDK-level retry-after-commit regression tests. Requires local Azurite.

Fault is injected at the HTTP transport so the Azure Tables SDK's own
retry machinery executes. Never use these synthetic receipts in production.
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
    AmbiguousCommit, FinanceConflict, FinanceDenied, PARTITION,
    reserve, scope_key,
)
from finance_recovery import settle_unique_receipt

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1", reason="Requires local Azurite opt-in")

CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


class FaultTransport(RequestsTransport):
    """Apply once at server, then report a transport failure to the SDK."""
    def __init__(self, status=None, lose_response=False):
        super().__init__()
        self.status, self.lose_response, self.fired = status, lose_response, False

    def send(self, request, **kw):
        if request.method == "POST" and "$batch" in request.url and not self.fired:
            self.fired = True
            RequestsTransport.send(self, request, **kw)
            if self.lose_response:
                raise ServiceResponseError("connection reset after send")
            r = requests.Response()
            r.status_code, r.reason, r.url = self.status, "injected", request.url
            r._content = json.dumps({
                "odata.error": {"code": "X", "message": {"lang": "en", "value": "x"}}
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
        plain.close()
        svc.close()


def seed(table, project):
    for key in ("global:fal", scope_key(project)):
        table.create_entity({
            "PartitionKey": PARTITION, "RowKey": key, "ceilingCents": 1000,
            "spentCents": 0, "reservedCents": 0, "schemaVersion": 1,
        })


@pytest.mark.parametrize("status", [500, 503, 504])
def test_reserve_applied_batch_sdk_resend_is_never_false_denial(tables, status):
    name, plain = tables
    project, job = str(uuid.uuid4()), str(uuid.uuid4())
    seed(plain, project)
    faulty = TableClient.from_connection_string(
        CONN, table_name=name, transport=FaultTransport(status=status),
        retry_backoff_factor=0.01)
    try:
        try:
            reserve(faulty, project, job, 30)
        except AmbiguousCommit:
            pass
        except (FinanceDenied, FinanceConflict) as exc:
            committed = plain.get_entity(
                partition_key=PARTITION, row_key="reservation:" + job)
            pytest.fail("Committed reservation %s falsely denied: %s" %
                        (committed["state"], type(exc).__name__))
        assert plain.get_entity(
            partition_key=PARTITION, row_key="global:fal")["reservedCents"] == 30
    finally:
        faulty.close()


def test_settle_applied_batch_sdk_resend_not_reported_as_retryable(tables):
    name, plain = tables
    project, job = str(uuid.uuid4()), str(uuid.uuid4())
    seed(plain, project)
    reserve(plain, project, job, 50)
    receipt = {"job_id": job, "amount_cents": 35, "receipt_id": "settle-" + job}
    ok = lambda j, req, rid, a, r: (
        r["job_id"] == j and r["amount_cents"] == a
        and req == "request-" + job and rid == r["receipt_id"]
    )
    faulty = TableClient.from_connection_string(
        CONN, table_name=name, transport=FaultTransport(status=503),
        retry_backoff_factor=0.01)
    try:
        try:
            settle_unique_receipt(
                faulty, job_id=job, actual_cents=35,
                provider_request_id="request-" + job,
                receipt_id=receipt["receipt_id"],
                provider_receipt=receipt, verify_provider_receipt=ok,
            )
        except AmbiguousCommit:
            pass
        except FinanceConflict:
            pytest.fail("Settled transaction misreported as retryable")
        assert plain.get_entity(
            partition_key=PARTITION, row_key="reservation:" + job)["state"] == "settled"
        assert plain.get_entity(
            partition_key=PARTITION, row_key="global:fal")["spentCents"] == 35
    finally:
        faulty.close()

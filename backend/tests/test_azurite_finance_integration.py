"""Azure Table transaction integration against local Azurite only.

Never uses production Azure. AZURITE_TABLE_TEST=1 explicitly opts in.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from azure.data.tables import TableServiceClient
from finance_controls import FinanceDenied, reserve, scope_key


AZURITE_CONNECTION = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_TABLE_TEST") != "1",
    reason="Azurite integration is opt-in and has no production credentials",
)


@pytest.fixture
def table():
    service = TableServiceClient.from_connection_string(AZURITE_CONNECTION)
    table_name = "TestRender" + uuid.uuid4().hex[:16]
    client = service.create_table(table_name)
    try:
        yield client
    finally:
        service.delete_table(table_name)
        service.close()


def initialize(table, project_id, ceiling):
    for key in (scope_key(project_id), "global:fal"):
        table.create_entity({
            "PartitionKey": "render-finance-v1",
            "RowKey": key,
            "ceilingCents": ceiling,
            "reservedCents": 0,
            "spentCents": 0,
            "schemaVersion": 1,
        })


def test_atomic_reservation_against_azurite(table):
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    initialize(table, project_id, 200)

    reserve(table, project_id, job_id, 80)
    global_limit = table.get_entity(
        partition_key="render-finance-v1", row_key="global:fal"
    )
    project_limit = table.get_entity(
        partition_key="render-finance-v1", row_key=scope_key(project_id)
    )
    saved = table.get_entity(
        partition_key="render-finance-v1", row_key="reservation:" + job_id
    )
    assert global_limit["reservedCents"] == 80
    assert project_limit["reservedCents"] == 80
    assert saved["state"] == "reserved"


def test_concurrent_azurite_reservations_stay_below_limit(table):
    project_id = str(uuid.uuid4())
    initialize(table, project_id, 100)
    job_ids = [str(uuid.uuid4()) for _ in range(8)]

    def attempt(job_id):
        try:
            reserve(table, project_id, job_id, 40)
            return True
        except FinanceDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, job_ids))

    global_limit = table.get_entity(
        partition_key="render-finance-v1", row_key="global:fal"
    )
    project_limit = table.get_entity(
        partition_key="render-finance-v1", row_key=scope_key(project_id)
    )
    assert sum(outcomes) <= 2
    assert global_limit["reservedCents"] == sum(outcomes) * 40
    assert project_limit["reservedCents"] == sum(outcomes) * 40

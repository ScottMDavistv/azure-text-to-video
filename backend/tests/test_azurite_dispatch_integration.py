"""Azurite Queue+Table SDK dispatch tests, including lost ACK and duplicates.

Run only with AZURITE_DISPATCH_TEST=1 and local emulator. Never use paid FAL.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient

from dispatch_recovery import (
    DispatchConflict, DispatchUncertain, LEASE_SECONDS, dispatch_queued_job,
)
from jobs import claim_job
from job_queue import parse_job_message

ACCOUNT = (
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
)
TABLE_CONNECTION = (
    "DefaultEndpointsProtocol=http;" + ACCOUNT
    + "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)
QUEUE_CONNECTION = (
    "DefaultEndpointsProtocol=http;" + ACCOUNT
    + "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
)
pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_DISPATCH_TEST") != "1",
    reason="Requires explicitly opted-in local Azurite queue/table emulator",
)


@pytest.fixture
def emulator():
    service = TableServiceClient.from_connection_string(TABLE_CONNECTION)
    table_name = "Disp" + uuid4().hex[:16]
    queue_name = "disp" + uuid4().hex[:16]
    table = service.create_table(table_name)
    queue = QueueClient.from_connection_string(QUEUE_CONNECTION, queue_name)
    queue.create_queue()
    try:
        yield table, queue
    finally:
        queue.delete_queue()
        queue.close()
        service.delete_table(table_name)
        service.close()


def seed(table, job_id):
    table.create_entity({
        "PartitionKey": "video", "RowKey": job_id,
        "status": "queued", "prompt": "offline emulator only",
        "dispatchState": "pending",
    })


def receive_all(queue):
    return list(queue.receive_messages(messages_per_page=32, visibility_timeout=30))


def test_real_sdk_durable_dispatch_and_conditional_job_claim(emulator):
    table, queue = emulator
    job_id = str(uuid4())
    seed(table, job_id)
    def send(value):
        queue.send_message('{"job_id":"' + value + '"}')

    assert dispatch_queued_job(job_id, table=table, send=send)["status"] == "sent"
    messages = receive_all(queue)
    assert len(messages) == 1
    assert parse_job_message(messages[0].content) == job_id
    with patch("jobs.get_jobs_table_client", return_value=table):
        assert claim_job(job_id) is not None
        assert claim_job(job_id) is None
    assert table.get_entity("video", job_id)["status"] == "processing"


def test_real_sdk_lost_queue_ack_and_redelivery_single_execution(emulator):
    table, queue = emulator
    job_id = str(uuid4())
    seed(table, job_id)
    now = datetime.now(timezone.utc)

    def accepted_but_lost(value):
        queue.send_message('{"job_id":"' + value + '"}')
        raise ConnectionError("simulated lost queue send acknowledgement")

    with pytest.raises(DispatchUncertain):
        dispatch_queued_job(job_id, table=table, send=accepted_but_lost, now=now)
    with pytest.raises(DispatchConflict):
        dispatch_queued_job(job_id, table=table, send=lambda _: None,
                            now=now + timedelta(seconds=1))
    dispatch_queued_job(
        job_id, table=table,
        send=lambda value: queue.send_message('{"job_id":"' + value + '"}'),
        now=now + timedelta(seconds=LEASE_SECONDS + 1),
    )
    messages = receive_all(queue)
    assert len(messages) == 2
    assert {parse_job_message(x.content) for x in messages} == {job_id}
    with patch("jobs.get_jobs_table_client", return_value=table):
        claims = [claim_job(job_id) for _ in messages]
    assert sum(x is not None for x in claims) == 1
    assert table.get_entity("video", job_id)["status"] == "processing"


def test_real_sdk_concurrent_dispatch_one_sender(emulator):
    from concurrent.futures import ThreadPoolExecutor
    table, queue = emulator
    job_id = str(uuid4())
    seed(table, job_id)
    def attempt(_):
        try:
            dispatch_queued_job(
                job_id, table=table,
                send=lambda value: queue.send_message(
                    '{"job_id":"' + value + '"}'))
            return True
        except DispatchConflict:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert sum(results) == 1
    assert len(receive_all(queue)) == 1

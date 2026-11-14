import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode

from job_queue import record_poison_message


def make_message(job_id=None):
    message = MagicMock()
    message.id = str(uuid4())
    message.pop_receipt = "receipt"
    message.dequeue_count = 5
    message.content = json.dumps({
        "job_id": job_id or str(uuid4()),
    })
    return message


def make_job(status):
    entity = MagicMock()
    entity.__getitem__.side_effect = {
        "status": status,
    }.__getitem__
    entity.metadata = {"etag": 'W/"12345"'}
    return entity


def test_queued_job_marked_failed():
    job_id = str(uuid4())
    message = make_message(job_id)

    table = MagicMock()
    table.get_entity.return_value = make_job("queued")

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        record_poison_message(
            message,
            "Delivery attempts exhausted",
        )

    table.upsert_entity.assert_called_once()

    kwargs = table.update_entity.call_args.kwargs

    assert kwargs["entity"]["RowKey"] == job_id
    assert kwargs["entity"]["status"] == "failed"
    assert kwargs["entity"]["errorType"] == (
        "QueueDeliveryLimitExceeded"
    )
    assert kwargs["mode"] == UpdateMode.MERGE
    assert kwargs["etag"] == 'W/"12345"'
    assert kwargs["match_condition"] == (
        MatchConditions.IfNotModified
    )


def test_processing_job_marked_failed():
    message = make_message()

    table = MagicMock()
    table.get_entity.return_value = make_job("processing")

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        record_poison_message(message, "Attempts exhausted")

    assert table.update_entity.call_args.kwargs[
        "entity"
    ]["status"] == "failed"


def test_completed_job_not_overwritten():
    message = make_message()

    table = MagicMock()
    table.get_entity.return_value = make_job("completed")

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        record_poison_message(message, "Duplicate message")

    table.upsert_entity.assert_called_once()
    table.update_entity.assert_not_called()


def test_missing_job_preserves_deadletter():
    message = make_message()

    table = MagicMock()
    table.get_entity.side_effect = ResourceNotFoundError(
        "Job not found"
    )

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        record_poison_message(message, "Orphaned message")

    table.upsert_entity.assert_called_once()
    table.update_entity.assert_not_called()


def test_malformed_message_preserves_deadletter():
    message = make_message()
    message.content = "invalid-json"

    table = MagicMock()

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        record_poison_message(message, "Malformed message")

    table.upsert_entity.assert_called_once()
    table.get_entity.assert_not_called()


def test_concurrent_update_error_propagates():
    message = make_message()

    table = MagicMock()
    table.get_entity.return_value = make_job("queued")
    table.update_entity.side_effect = HttpResponseError(
        message="ETag mismatch",
        status_code=412,
    )

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        with pytest.raises(HttpResponseError):
            record_poison_message(
                message,
                "Attempts exhausted",
            )


def test_deadletter_write_error_propagates():
    message = make_message()

    table = MagicMock()
    table.upsert_entity.side_effect = RuntimeError(
        "Storage unavailable"
    )

    with patch(
        "job_queue.get_jobs_table_client",
        return_value=table,
    ):
        with pytest.raises(RuntimeError):
            record_poison_message(
                message,
                "Storage failure",
            )

    table.update_entity.assert_not_called()

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from consumer import consume_one
from job_queue import record_poison_message


def make_message(attempts, content=None):
    message = MagicMock()
    message.id = str(uuid4())
    message.pop_receipt = "receipt-123"
    message.dequeue_count = attempts
    message.content = (
        content
        if content is not None
        else json.dumps({"job_id": str(uuid4())})
    )
    return message


def test_poison_message_preserved_then_deleted():
    queue = MagicMock()
    message = make_message(5)
    queue.receive_messages.return_value = [message]

    processor = MagicMock()
    handler = MagicMock()

    result = consume_one(
        processor=processor,
        queue_client=queue,
        poison_handler=handler,
    )

    assert result["status"] == "deadlettered"
    assert result["attempts"] == 5

    handler.assert_called_once()
    processor.assert_not_called()
    queue.delete_message.assert_called_once_with(
        message.id,
        message.pop_receipt,
    )


def test_deadletter_storage_failure_preserves_queue_message():
    queue = MagicMock()
    message = make_message(5)
    queue.receive_messages.return_value = [message]

    handler = MagicMock(
        side_effect=RuntimeError("Table unavailable")
    )

    with pytest.raises(RuntimeError):
        consume_one(
            processor=MagicMock(),
            queue_client=queue,
            poison_handler=handler,
        )

    queue.delete_message.assert_not_called()


def test_normal_message_processes():
    queue = MagicMock()
    message = make_message(2)
    queue.receive_messages.return_value = [message]

    processor = MagicMock(
        return_value={"status": "completed"}
    )

    result = consume_one(
        processor=processor,
        queue_client=queue,
    )

    assert result["status"] == "acknowledged"
    assert result["attempts"] == 2
    processor.assert_called_once()


def test_invalid_message_retries_below_limit():
    queue = MagicMock()
    message = make_message(2, "invalid-json")
    queue.receive_messages.return_value = [message]

    with pytest.raises(ValueError):
        consume_one(
            processor=MagicMock(),
            queue_client=queue,
        )

    queue.delete_message.assert_not_called()


def test_invalid_message_deadletters_at_limit():
    queue = MagicMock()
    message = make_message(5, "invalid-json")
    queue.receive_messages.return_value = [message]

    handler = MagicMock()

    result = consume_one(
        processor=MagicMock(),
        queue_client=queue,
        poison_handler=handler,
    )

    assert result["status"] == "deadlettered"
    handler.assert_called_once()
    queue.delete_message.assert_called_once()


def test_deadletter_record_written_to_table():
    message = make_message(5, "invalid-json")

    with patch("job_queue.get_jobs_table_client") as table:
        record = record_poison_message(
            message,
            "Invalid message",
        )

    assert record["PartitionKey"] == "deadletter"
    assert record["RowKey"] == message.id
    assert record["attempts"] == 5

    table.return_value.upsert_entity.assert_called_once()


def test_invalid_attempt_limit():
    with pytest.raises(ValueError):
        consume_one(
            processor=MagicMock(),
            max_attempts=0,
        )

import json

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from consumer import consume_one


def make_message(job_id):
    message = MagicMock()
    message.id = "message-123"
    message.pop_receipt = "receipt-123"
    message.content = json.dumps({
        "job_id": job_id,
    })
    return message


def test_empty_queue():
    queue = MagicMock()
    queue.receive_messages.return_value = []

    result = consume_one(
        processor=MagicMock(),
        queue_client=queue,
    )

    assert result["status"] == "empty"
    queue.delete_message.assert_not_called()


def test_successful_processing():
    job_id = str(uuid4())

    queue = MagicMock()
    queue.receive_messages.return_value = [
        make_message(job_id)
    ]

    processor = MagicMock(
        return_value={"status": "completed"}
    )

    result = consume_one(
        processor=processor,
        queue_client=queue,
    )

    assert result["status"] == "acknowledged"
    assert result["job_id"] == job_id

    processor.assert_called_once_with(job_id)

    queue.delete_message.assert_called_once_with(
        "message-123",
        "receipt-123",
    )


def test_processing_failure_preserves_message():
    job_id = str(uuid4())

    queue = MagicMock()
    queue.receive_messages.return_value = [
        make_message(job_id)
    ]

    processor = MagicMock(
        side_effect=RuntimeError("Renderer failed")
    )

    with pytest.raises(RuntimeError):
        consume_one(
            processor=processor,
            queue_client=queue,
        )

    queue.delete_message.assert_not_called()


def test_invalid_message_preserves_message():
    queue = MagicMock()

    message = MagicMock()
    message.content = json.dumps({
        "job_id": "invalid-id"
    })

    queue.receive_messages.return_value = [message]

    with pytest.raises(ValueError):
        consume_one(
            processor=MagicMock(),
            queue_client=queue,
        )

    queue.delete_message.assert_not_called()


def test_processor_required():
    with pytest.raises(ValueError):
        consume_one(processor=None)

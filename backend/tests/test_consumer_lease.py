import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from consumer import consume_one


def make_message(attempts=1):
    message = MagicMock()
    message.id = "message-123"
    message.pop_receipt = "original-receipt"
    message.dequeue_count = attempts
    message.content = json.dumps({
        "job_id": str(uuid4()),
    })
    return message


def make_lease(receipt="renewed-receipt"):
    lease = MagicMock()
    lease.assert_healthy = MagicMock()
    lease.pop_receipt = receipt
    lease.__enter__.return_value = lease
    lease.__exit__.return_value = False
    return lease


def test_renewed_receipt_used_for_delete():
    queue = MagicMock()
    message = make_message()
    queue.receive_messages.return_value = [message]

    lease = make_lease()

    with patch(
        "consumer.QueueVisibilityLease",
        return_value=lease,
    ):
        result = consume_one(
            processor=lambda job_id: {
                "status": "completed"
            },
            queue_client=queue,
        )

    assert result["status"] == "acknowledged"

    lease.assert_healthy.assert_called_once()

    queue.delete_message.assert_called_once_with(
        message.id,
        "renewed-receipt",
    )


def test_renewal_failure_prevents_acknowledgment():
    queue = MagicMock()
    message = make_message()
    queue.receive_messages.return_value = [message]

    lease = make_lease()
    lease.assert_healthy.side_effect = RuntimeError(
        "Visibility renewal failed"
    )

    with patch(
        "consumer.QueueVisibilityLease",
        return_value=lease,
    ):
        with pytest.raises(RuntimeError):
            consume_one(
                processor=lambda job_id: {
                    "status": "completed"
                },
                queue_client=queue,
            )

    queue.delete_message.assert_not_called()


def test_processing_failure_preserves_message():
    queue = MagicMock()
    message = make_message()
    queue.receive_messages.return_value = [message]

    lease = make_lease()

    with patch(
        "consumer.QueueVisibilityLease",
        return_value=lease,
    ):
        with pytest.raises(ValueError):
            consume_one(
                processor=lambda job_id: (
                    (_ for _ in ()).throw(
                        ValueError("Rendering failed")
                    )
                ),
                queue_client=queue,
            )

    queue.delete_message.assert_not_called()
    lease.__exit__.assert_called_once()


def test_deadletter_uses_renewed_receipt():
    queue = MagicMock()
    message = make_message(attempts=5)
    queue.receive_messages.return_value = [message]

    lease = make_lease("poison-renewed-receipt")
    handler = MagicMock()
    processor = MagicMock()

    with patch(
        "consumer.QueueVisibilityLease",
        return_value=lease,
    ):
        result = consume_one(
            processor=processor,
            queue_client=queue,
            poison_handler=handler,
        )

    assert result["status"] == "deadlettered"
    processor.assert_not_called()
    handler.assert_called_once()

    queue.delete_message.assert_called_once_with(
        message.id,
        "poison-renewed-receipt",
    )


def test_deadletter_renewal_failure_prevents_delete():
    queue = MagicMock()
    message = make_message(attempts=5)
    queue.receive_messages.return_value = [message]

    lease = make_lease()
    lease.assert_healthy.side_effect = RuntimeError(
        "Renewal failed"
    )

    with patch(
        "consumer.QueueVisibilityLease",
        return_value=lease,
    ):
        with pytest.raises(RuntimeError):
            consume_one(
                processor=MagicMock(),
                queue_client=queue,
                poison_handler=MagicMock(),
            )

    queue.delete_message.assert_not_called()


def test_invalid_renewal_settings():
    with pytest.raises(ValueError):
        consume_one(
            processor=MagicMock(),
            visibility_timeout=120,
            renew_interval=120,
        )

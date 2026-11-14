from unittest.mock import MagicMock

import pytest

from queue_lease import QueueVisibilityLease


def make_lease():
    queue = MagicMock()
    message = MagicMock()

    message.id = "message-123"
    message.pop_receipt = "receipt-original"

    queue.update_message.return_value.pop_receipt = (
        "receipt-renewed"
    )

    lease = QueueVisibilityLease(
        queue_client=queue,
        message=message,
        visibility_timeout=120,
        renew_interval=60,
    )

    return lease, queue


def test_lease_renews_message():
    lease, queue = make_lease()

    receipt = lease.renew()

    assert receipt == "receipt-renewed"
    assert lease.pop_receipt == "receipt-renewed"

    queue.update_message.assert_called_once_with(
        "message-123",
        "receipt-original",
        visibility_timeout=120,
    )


def test_lease_uses_latest_receipt():
    lease, queue = make_lease()

    lease.renew()
    lease.renew()

    assert queue.update_message.call_count == 2

    second_call = queue.update_message.call_args_list[1]

    assert second_call.args[1] == "receipt-renewed"


def test_lease_context_manager():
    lease, queue = make_lease()

    with lease:
        assert lease._thread is not None
        lease.assert_healthy()

    assert lease._thread is None


def test_invalid_visibility_timeout():
    with pytest.raises(ValueError):
        QueueVisibilityLease(
            MagicMock(),
            MagicMock(),
            visibility_timeout=0,
        )


def test_invalid_renew_interval():
    with pytest.raises(ValueError):
        QueueVisibilityLease(
            MagicMock(),
            MagicMock(),
            visibility_timeout=120,
            renew_interval=120,
        )


def test_renewal_failure_is_reported():
    lease, queue = make_lease()

    queue.update_message.side_effect = RuntimeError(
        "Azure Queue unavailable"
    )

    # Direct renewal raises the underlying SDK error.
    with pytest.raises(RuntimeError):
        lease.renew()


def test_cannot_start_twice():
    lease, queue = make_lease()

    try:
        lease.start()

        with pytest.raises(RuntimeError):
            lease.start()
    finally:
        lease.stop()

"""
Azure Storage Queue consumer with visibility renewal,
bounded retries, and poison-message handling.

No automatic polling or rendering service is enabled.
"""

from job_queue import (
    get_queue_client,
    parse_job_message,
    record_poison_message,
)
from queue_lease import QueueVisibilityLease


MAX_DELIVERY_ATTEMPTS = 5


def consume_one(
    processor,
    queue_client=None,
    poison_handler=None,
    max_attempts=MAX_DELIVERY_ATTEMPTS,
    visibility_timeout=120,
    renew_interval=60,
):
    if processor is None:
        raise ValueError("A job processor is required")

    if (type(max_attempts) is not int or max_attempts < 1
            or max_attempts > 100):
        raise ValueError("max_attempts must be an integer between 1 and 100")

    if (type(visibility_timeout) is not int
            or not 1 <= visibility_timeout <= 604800):
        raise ValueError("Invalid visibility timeout")

    if (type(renew_interval) not in (int, float)
            or not 0 < renew_interval < visibility_timeout):
        raise ValueError("Invalid visibility renewal interval")

    queue = (
        queue_client
        if queue_client is not None
        else get_queue_client()
    )

    handler = (
        poison_handler
        if poison_handler is not None
        else record_poison_message
    )

    messages = queue.receive_messages(
        max_messages=1,
        visibility_timeout=visibility_timeout,
    )

    message = next(iter(messages), None)

    if message is None:
        return {
            "status": "empty",
            "processed": False,
        }

    count = getattr(message, "dequeue_count", None)
    attempts = count if type(count) is int and count >= 1 else 1

    lease = QueueVisibilityLease(
        queue_client=queue,
        message=message,
        visibility_timeout=visibility_timeout,
        renew_interval=renew_interval,
    )

    # Keep renewing the message's invisibility while the
    # processor or poison handler is working.
    with lease:
        if attempts >= max_attempts:
            handler(
                message,
                f"Delivery attempt limit reached: {attempts}",
            )

            result = {
                "status": "deadlettered",
                "processed": False,
                "attempts": attempts,
            }

        else:
            job_id = parse_job_message(message.content)

            # Processing failures propagate without deleting
            # the queue message.
            processed = processor(job_id)

            result = {
                "status": "acknowledged",
                "processed": True,
                "job_id": job_id,
                "result": processed,
                "attempts": attempts,
            }

    # The lease thread has stopped at this point.
    # Never acknowledge a message if visibility renewal failed.
    lease.assert_healthy()

    # Renewal changes the pop receipt. Always use the newest one.
    queue.delete_message(
        message.id,
        lease.pop_receipt,
    )

    return result

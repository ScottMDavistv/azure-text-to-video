"""
Azure Storage Queue consumer with bounded delivery attempts.

No automatic polling or real renderer is enabled yet.
"""

from job_queue import (
    get_queue_client,
    parse_job_message,
    record_poison_message,
)

MAX_DELIVERY_ATTEMPTS = 5


def consume_one(
    processor,
    queue_client=None,
    poison_handler=None,
    max_attempts=MAX_DELIVERY_ATTEMPTS,
):
    if processor is None:
        raise ValueError("A job processor is required")

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    queue = queue_client if queue_client is not None else get_queue_client()
    handler = (
        poison_handler
        if poison_handler is not None
        else record_poison_message
    )

    messages = queue.receive_messages(
        max_messages=1,
        visibility_timeout=120,
    )

    message = next(iter(messages), None)

    if message is None:
        return {
            "status": "empty",
            "processed": False,
        }

    count = getattr(message, "dequeue_count", None)
    attempts = count if isinstance(count, int) else 1

    if attempts >= max_attempts:
        # Persist the dead-letter record BEFORE deleting the message.
        # If persistence fails, the message stays in the queue.
        handler(
            message,
            f"Delivery attempt limit reached: {attempts}",
        )

        queue.delete_message(
            message.id,
            message.pop_receipt,
        )

        return {
            "status": "deadlettered",
            "processed": False,
            "attempts": attempts,
        }

    job_id = parse_job_message(message.content)

    # Processing exceptions propagate. The message remains
    # available for another attempt after its visibility timeout.
    result = processor(job_id)

    queue.delete_message(
        message.id,
        message.pop_receipt,
    )

    return {
        "status": "acknowledged",
        "processed": True,
        "job_id": job_id,
        "result": result,
        "attempts": attempts,
    }

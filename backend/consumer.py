"""
Azure Storage Queue consumer foundation.

No polling loop or rendering provider is enabled yet.
Call consume_one() with an explicitly supplied processor.
"""

from job_queue import get_queue_client, parse_job_message


def consume_one(processor, queue_client=None):
    """
    Receive at most one job.

    Delete the queue message only after the processor succeeds.
    On failure, leave it in the queue for retry.
    """
    if processor is None:
        raise ValueError("A job processor is required")

    queue = queue_client or get_queue_client()

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

    job_id = parse_job_message(message.content)

    # Exceptions deliberately propagate. Azure Queue will make
    # the undeleted message visible again after the timeout.
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
    }

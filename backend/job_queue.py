import json
import os
from uuid import UUID

from azure.storage.queue import QueueClient

from storage import STORAGE_ACCOUNT, get_credential, get_jobs_table_client


QUEUE_NAME = os.getenv("AZURE_JOBS_QUEUE", "textvideo-jobs")


def get_queue_client():
    return QueueClient(
        account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
        queue_name=QUEUE_NAME,
        credential=get_credential(),
    )


def enqueue_job(job_id):
    validated_id = str(UUID(str(job_id)))

    message = json.dumps({
        "job_id": validated_id,
    })

    get_queue_client().send_message(message)

    return {
        "job_id": validated_id,
        "queued": True,
    }


def parse_job_message(message):
    """Require a bounded JSON object with a canonical UUID job identifier."""
    if not isinstance(message, str) or len(message) > 4096:
        raise ValueError("Invalid queue message")
    payload = json.loads(message)
    if not isinstance(payload, dict):
        raise ValueError("Invalid queue message object")
    job_id = payload["job_id"]
    if not isinstance(job_id, str):
        raise ValueError("Invalid queue job identifier")
    canonical = str(UUID(job_id))
    if canonical != job_id:
        raise ValueError("Non-canonical queue job identifier")
    return canonical



def record_poison_message(message, reason):
    """
    Record a poisoned message and reconcile its associated job.

    The queue message must only be deleted after this function
    completes successfully.
    """
    from datetime import datetime, timezone

    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceNotFoundError
    from azure.data.tables import UpdateMode


    now = datetime.now(timezone.utc).isoformat()
    table = get_jobs_table_client()

    attempts = getattr(message, "dequeue_count", None)
    if not isinstance(attempts, int):
        attempts = 0

    record = {
        "PartitionKey": "deadletter",
        "RowKey": str(message.id),
        "reason": str(reason)[:500],
        "content": str(message.content)[:4000],
        "attempts": attempts,
        "recordedAt": now,
    }

    # Persist evidence of failure before modifying job state.
    table.upsert_entity(entity=record)

    try:
        job_id = parse_job_message(message.content)
    except (ValueError, KeyError, TypeError):
        # Malformed messages cannot be associated with a job.
        return record

    try:
        job = table.get_entity(
            partition_key="video",
            row_key=job_id,
        )
    except ResourceNotFoundError:
        # The dead-letter record preserves orphaned messages.
        return record

    # Never overwrite terminal job states.
    if job["status"] not in {"queued", "processing"}:
        return record

    # Conditional update prevents overwriting a concurrent
    # worker's newer status or completion result.
    table.update_entity(
        entity={
            "PartitionKey": "video",
            "RowKey": job_id,
            "status": "failed",
            "errorType": "QueueDeliveryLimitExceeded",
            "failedAt": now,
        },
        mode=UpdateMode.MERGE,
        etag=job.metadata["etag"],
        match_condition=MatchConditions.IfNotModified,
    )

    return record

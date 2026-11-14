import json
import os
from uuid import UUID

from azure.storage.queue import QueueClient

from storage import STORAGE_ACCOUNT, get_credential


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
    payload = json.loads(message)
    return str(UUID(payload["job_id"]))


def record_poison_message(message, reason):
    """
    Preserve a failed queue message in the existing VideoJobs table.

    A separate partition keeps dead-letter records distinct
    from normal video jobs.
    """
    from datetime import datetime, timezone
    from storage import get_jobs_table_client

    attempts = getattr(message, "dequeue_count", None)
    if not isinstance(attempts, int):
        attempts = 0

    record = {
        "PartitionKey": "deadletter",
        "RowKey": str(message.id),
        "reason": str(reason)[:500],
        "content": str(message.content)[:4000],
        "attempts": attempts,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }

    get_jobs_table_client().upsert_entity(entity=record)
    return record

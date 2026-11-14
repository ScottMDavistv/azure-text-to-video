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

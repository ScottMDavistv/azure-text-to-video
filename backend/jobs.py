"""Legacy job state transitions.

All transitions are compare-and-swap operations so a stale worker cannot
overwrite a concurrent cancellation or terminal status.
"""
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode
from storage import get_jobs_table_client

VALID_TRANSITIONS = {
    "created": {"queued"},
    "queued": {"processing"},
    "processing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


class JobTransitionConflict(ValueError):
    """A transition was concurrently changed or cannot be safely committed."""


def transition_job(job_id, next_status, **fields):
    # Caller-supplied metadata must never replace row identity or status.
    if {"PartitionKey", "RowKey", "status"}.intersection(fields):
        raise ValueError("Cannot override job identity or transition status")

    table = get_jobs_table_client()
    entity = table.get_entity(partition_key="video", row_key=str(job_id))
    current_status = entity["status"]

    if next_status not in VALID_TRANSITIONS.get(current_status, set()):
        raise ValueError(
            f"Cannot transition from {current_status} to {next_status}"
        )

    etag = (getattr(entity, "metadata", None) or {}).get("etag")
    if not isinstance(etag, str) or not etag:
        raise JobTransitionConflict("Missing concurrency ETag")

    update = {
        "PartitionKey": "video",
        "RowKey": str(job_id),
        "status": next_status,
        **fields,
    }
    try:
        table.update_entity(
            entity=update,
            mode=UpdateMode.MERGE,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        if exc.status_code in (409, 412):
            raise JobTransitionConflict("Job changed during transition") from exc
        raise

    return update


def claim_job(job_id):
    """Atomically claim a queued job using its Azure Table ETag.

    Returns the original job entity when the claim succeeds.
    Returns None if the job is already claimed or not queued.
    """
    table = get_jobs_table_client()
    entity = table.get_entity(partition_key="video", row_key=str(job_id))
    if entity["status"] != "queued":
        return None

    etag = entity.metadata["etag"]
    from datetime import datetime, timezone

    try:
        table.update_entity(
            entity={
                "PartitionKey": "video",
                "RowKey": str(job_id),
                "status": "processing",
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
            mode=UpdateMode.MERGE,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        if exc.status_code in (409, 412):
            return None
        raise
    return entity

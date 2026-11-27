"""
Detection and manual recovery of stale video jobs.

No automatic recovery is enabled.

A stale timestamp does NOT prove the worker has stopped.
Confirm the worker is no longer processing a job before
performing manual recovery.
"""

from datetime import datetime, timedelta, timezone

from azure.core import MatchConditions
from azure.data.tables import UpdateMode

from storage import get_jobs_table_client
from project_models import validate_id


DEFAULT_STALE_MINUTES = 60


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")

    timestamp = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")

    return timestamp.astimezone(timezone.utc)


def is_stale(entity, now=None, stale_minutes=DEFAULT_STALE_MINUTES):
    if type(stale_minutes) is not int or not 1 <= stale_minutes <= 10080:
        raise ValueError("Invalid stale-job threshold")

    if entity.get("status") != "processing":
        return False

    started_at = entity.get("startedAt")

    if not started_at:
        return False

    now = now or datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("now must include a timezone")

    started = parse_timestamp(started_at)

    return now - started > timedelta(
        minutes=stale_minutes
    )


def find_stale_jobs(now=None, stale_minutes=DEFAULT_STALE_MINUTES, limit=50):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Stale job limit must be between 1 and 50")
    if type(stale_minutes) is not int or not 1 <= stale_minutes <= 10080:
        raise ValueError("Invalid stale-job threshold")
    table = get_jobs_table_client()

    entities = table.query_entities(
        query_filter=(
            "PartitionKey eq 'video' "
            "and status eq 'processing'"
        )
    )

    results = []

    for entity in entities:
        if is_stale(
            entity,
            now=now,
            stale_minutes=stale_minutes,
        ):
            results.append({
                "job_id": entity["RowKey"],
                "status": entity["status"],
                "started_at": entity["startedAt"],
            })
            if len(results) >= limit:
                break

    return results


def recover_stale_job(
    job_id,
    *,
    confirmed_worker_stopped=False,
    now=None,
    stale_minutes=DEFAULT_STALE_MINUTES,
):
    """
    Manual recovery only.

    Requires explicit confirmation that the worker
    is no longer processing this job.

    Uses an ETag to reject concurrent table updates.
    """
    if confirmed_worker_stopped is not True:
        raise PermissionError(
            "Worker shutdown must be confirmed before recovery"
        )

    validate_id(job_id)
    if type(stale_minutes) is not int or not 1 <= stale_minutes <= 10080:
        raise ValueError("Invalid stale-job threshold")
    table = get_jobs_table_client()

    entity = table.get_entity(
        partition_key="video",
        row_key=str(job_id),
    )

    if not is_stale(
        entity,
        now=now,
        stale_minutes=stale_minutes,
    ):
        raise ValueError(
            "Job is not eligible for stale-job recovery"
        )

    recovered_at = (
        now or datetime.now(timezone.utc)
    ).isoformat()

    table.update_entity(
        entity={
            "PartitionKey": "video",
            "RowKey": str(job_id),
            "status": "failed",
            "errorType": "StaleJobRecovery",
            "failedAt": recovered_at,
        },
        mode=UpdateMode.MERGE,
        etag=entity.metadata["etag"],
        match_condition=MatchConditions.IfNotModified,
    )

    return {
        "job_id": str(job_id),
        "status": "failed",
        "recovery": "manual",
    }

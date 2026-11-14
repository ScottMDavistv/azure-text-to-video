"""
Administrative job listing, inspection and cancellation.

Job cancellation only succeeds for created or queued jobs.

Azure Table ETags prevent an administrative cancellation
from overwriting a concurrent worker claim.

The listing cursor is RowKey-based, not chronological.
"""

from datetime import datetime, timezone
from itertools import islice
from uuid import UUID

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode

from storage import get_jobs_table_client


ALLOWED_STATUSES = frozenset({
    "created",
    "queued",
    "processing",
    "completed",
    "failed",
    "cancelled",
})

CANCELLABLE_STATUSES = frozenset({
    "created",
    "queued",
})

MAX_PAGE_SIZE = 50


class JobNotFoundError(LookupError):
    pass


class JobConflictError(RuntimeError):
    pass


def normalize_job_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid job ID") from None


def validate_page_size(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_PAGE_SIZE
    ):
        raise ValueError(
            f"Page size must be between 1 and {MAX_PAGE_SIZE}"
        )
    return value


def validate_status(status):
    if status is not None and status not in ALLOWED_STATUSES:
        raise ValueError("Unsupported job status")
    return status


def project_job(entity):
    """
    Return a bounded, secret-free projection.

    Administrative listing includes prompts, so the route
    must remain restricted to trusted administrators.
    """
    return {
        "job_id": entity["RowKey"],
        "status": entity.get("status", "unknown"),
        "prompt": entity.get("prompt"),
        "created_at": entity.get("createdAt"),
        "started_at": entity.get("startedAt"),
        "completed_at": entity.get("completedAt"),
        "failed_at": entity.get("failedAt"),
        "cancelled_at": entity.get("cancelledAt"),
    }


def list_jobs(
    *,
    status=None,
    limit=20,
    cursor=None,
    table=None,
):
    validate_status(status)
    validate_page_size(limit)

    if cursor is not None:
        cursor = normalize_job_id(cursor)

    if table is None:
        table = get_jobs_table_client()

    # RowKey is a UUID. Azure Table query results are
    # ordered by PartitionKey and RowKey.
    filters = ["PartitionKey eq 'video'"]

    if status is not None:
        filters.append(f"status eq '{status}'")

    if cursor is not None:
        filters.append(f"RowKey gt '{cursor}'")

    query_filter = " and ".join(filters)

    entities = table.query_entities(
        query_filter=query_filter,
        results_per_page=limit + 1,
    )

    # Read one additional result to identify another page.
    page = list(islice(entities, limit + 1))

    has_more = len(page) > limit
    selected = page[:limit]

    next_cursor = (
        selected[-1]["RowKey"]
        if has_more and selected
        else None
    )

    return {
        "jobs": [project_job(item) for item in selected],
        "count": len(selected),
        "limit": limit,
        "status_filter": status,
        "next_cursor": next_cursor,
        "ordering": "row_key_ascending",
    }


def get_job_details(job_id, *, table=None):
    job_id = normalize_job_id(job_id)

    if table is None:
        table = get_jobs_table_client()

    try:
        entity = table.get_entity(
            partition_key="video",
            row_key=job_id,
        )
    except ResourceNotFoundError:
        raise JobNotFoundError(
            "Video job not found"
        ) from None

    return project_job(entity)


def cancel_job(
    job_id,
    *,
    table=None,
    now=None,
):
    job_id = normalize_job_id(job_id)

    if table is None:
        table = get_jobs_table_client()

    try:
        entity = table.get_entity(
            partition_key="video",
            row_key=job_id,
        )
    except ResourceNotFoundError:
        raise JobNotFoundError(
            "Video job not found"
        ) from None

    status = entity.get("status")

    if status not in CANCELLABLE_STATUSES:
        raise JobConflictError(
            "Only created or queued jobs can be cancelled"
        )

    metadata = getattr(entity, "metadata", None) or {}
    etag = metadata.get("etag")

    if not etag:
        raise JobConflictError(
            "Cannot cancel job without a valid ETag"
        )

    cancelled_at = (
        now or datetime.now(timezone.utc)
    ).isoformat()

    updated = {
        "PartitionKey": "video",
        "RowKey": job_id,
        "status": "cancelled",
        "cancelledAt": cancelled_at,
    }

    try:
        table.update_entity(
            entity=updated,
            mode=UpdateMode.MERGE,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        if exc.status_code in (409, 412):
            raise JobConflictError(
                "Job changed during cancellation"
            ) from exc
        raise
    except ResourceNotFoundError:
        raise JobNotFoundError(
            "Video job not found"
        ) from None

    return {
        "job_id": job_id,
        "status": "cancelled",
        "cancelled_at": cancelled_at,
        "previous_status": status,
    }

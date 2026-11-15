"""Durable Fal submission state. No provider network calls."""

from datetime import datetime, timezone
from uuid import UUID

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceNotFoundError,
    HttpResponseError,
)
from azure.data.tables import UpdateMode

PARTITION = "fal-submission-intents"


class SubmissionBlocked(RuntimeError):
    pass


def _job_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SubmissionBlocked("Invalid video job ID") from exc


def create_intent(table, job_id):
    """Create at most one submission intent per job."""
    job_id = _job_id(job_id)

    entity = {
        "PartitionKey": PARTITION,
        "RowKey": job_id,
        "state": "prepared",
        "providerRequestId": "",
        "createdAt": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    try:
        table.create_entity(entity)
    except ResourceExistsError as exc:
        raise SubmissionBlocked(
            "Submission intent already exists"
        ) from exc

    return job_id


def claim_intent(table, job_id):
    """Atomically transition prepared -> submitting.

    A submitting state must NEVER be automatically
    reset: a provider request may have been accepted.
    """
    job_id = _job_id(job_id)

    try:
        entity = table.get_entity(
            partition_key=PARTITION,
            row_key=job_id,
        )
    except ResourceNotFoundError as exc:
        raise SubmissionBlocked(
            "Submission intent not found"
        ) from exc

    if entity["state"] != "prepared":
        raise SubmissionBlocked(
            "Submission already claimed or unresolved"
        )

    updated = dict(entity)
    updated["state"] = "submitting"

    try:
        table.update_entity(
            entity=updated,
            mode=UpdateMode.REPLACE,
            etag=entity.metadata["etag"],
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise SubmissionBlocked(
            "Concurrent claim or storage error; "
            "do not submit"
        ) from exc

    return {
        "job_id": job_id,
        "state": "submitting",
        "automatic_retry_allowed": False,
    }


def read_intent(table, job_id):
    """Read state without changing it."""
    job_id = _job_id(job_id)

    return table.get_entity(
        partition_key=PARTITION,
        row_key=job_id,
    )

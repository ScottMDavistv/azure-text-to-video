"""Durable, lease-based dispatch recovery for the legacy non-paid job queue.

Azure Queue delivery is at-least-once. A lost send acknowledgement can mean
the message was accepted. Redelivery is allowed only after a durable dispatch
lease expires; duplicate render execution is prevented by jobs.claim_job's
ETag-conditional queued->processing transition, NOT by queue deduplication.

No background sweeper is started. Do not use for paid provider submission.
"""
from datetime import datetime, timedelta, timezone
from itertools import islice
from uuid import UUID, uuid4

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables import UpdateMode

from storage import get_jobs_table_client

LEASE_SECONDS = 300
REDISPATCH_AFTER_SECONDS = 900
MAX_DISPATCH_ATTEMPTS = 20


class DispatchConflict(RuntimeError):
    pass


class DispatchUncertain(RuntimeError):
    """The queue send or its acknowledgement may have committed."""


def _utc(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _timestamp(value):
    if not isinstance(value, str):
        raise DispatchConflict("Missing or malformed dispatch timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DispatchConflict("Malformed dispatch timestamp") from exc
    if parsed.tzinfo is None:
        raise DispatchConflict("Dispatch timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical UUID required")
    return value


def _etag(entity):
    value = (getattr(entity, "metadata", None) or {}).get("etag")
    if not isinstance(value, str) or not value:
        raise DispatchConflict("Dispatch state lacks concurrency ETag")
    return value


def _eligible(entity, now):
    if entity.get("status") != "queued":
        raise DispatchConflict("Only queued jobs can be dispatched")
    state = entity.get("dispatchState", "pending")
    if state not in {"pending", "sending", "sent"}:
        raise DispatchConflict("Unknown dispatch state")
    if state == "sending":
        if now < _timestamp(entity.get("dispatchLeaseUntil")):
            raise DispatchConflict("Another dispatch attempt holds the lease")
    if state == "sent":
        if now < (_timestamp(entity.get("dispatchUpdatedAt")) +
                  timedelta(seconds=REDISPATCH_AFTER_SECONDS)):
            return False
    return True


def dispatch_queued_job(job_id, *, table=None, send=None, now=None):
    """Atomically claim a dispatch attempt and send at most once per lease.

    On an uncertain outcome, retain the durable 'sending' record until its
    lease expires; callers must not immediately retry. A re-delivered queue
    message is safe only because the worker claims the job using an ETag.
    """
    from job_queue import enqueue_job

    job_id = _id(job_id)
    now = _utc(now)
    table = table if table is not None else get_jobs_table_client()
    send = send if send is not None else enqueue_job

    entity = table.get_entity(partition_key="video", row_key=job_id)
    if not _eligible(entity, now):
        return {"job_id": job_id, "status": "already_dispatched",
                "dispatched": False}

    attempts = entity.get("dispatchAttempts", 0)
    if type(attempts) is not int or attempts < 0 or attempts >= MAX_DISPATCH_ATTEMPTS:
        raise DispatchConflict("Dispatch attempt cap reached; inspect manually")

    token = str(uuid4())
    lease_end = now + timedelta(seconds=LEASE_SECONDS)
    update = {
        "PartitionKey": "video", "RowKey": job_id,
        "dispatchState": "sending",
        "dispatchAttemptId": token,
        "dispatchAttempts": attempts + 1,
        "dispatchLeaseUntil": lease_end.isoformat(),
        "dispatchUpdatedAt": now.isoformat(),
    }
    try:
        table.update_entity(
            entity=update, mode=UpdateMode.MERGE, etag=_etag(entity),
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        if exc.status_code in (409, 412):
            raise DispatchConflict("Dispatch lease acquired concurrently") from exc
        raise DispatchUncertain("Dispatch lease write may have committed") from exc
    except Exception as exc:
        raise DispatchUncertain("Dispatch lease write may have committed") from exc

    try:
        send(job_id)
    except Exception as exc:
        # Never revert the lease on a lost send response: it may have committed.
        raise DispatchUncertain("Queue delivery outcome unknown; reconcile later") from exc

    try:
        latest = table.get_entity(partition_key="video", row_key=job_id)
        if latest.get("dispatchAttemptId") != token:
            raise DispatchUncertain("Dispatch lease ownership changed")
        if latest.get("status") != "queued":
            # The message may already be claimed or cancelled. No resend.
            return {"job_id": job_id, "status": "state_advanced",
                    "dispatched": True}
        table.update_entity(
            entity={"PartitionKey": "video", "RowKey": job_id,
                    "dispatchState": "sent",
                    "dispatchUpdatedAt": _utc().isoformat()},
            mode=UpdateMode.MERGE, etag=_etag(latest),
            match_condition=MatchConditions.IfNotModified,
        )
    except DispatchUncertain:
        raise
    except Exception as exc:
        raise DispatchUncertain("Queue accepted; dispatch confirmation unknown") from exc

    return {"job_id": job_id, "status": "sent", "dispatched": True}


def reconcile_queued_jobs(*, table=None, send=None, now=None, limit=50):
    """Explicit, bounded recovery sweep; never auto-started by API or worker.

    Reinspection is necessary after crashes. An old sent marker is *not*
    proof the queue still holds the message. May enqueue duplicates but cannot
    cause duplicate worker claims when the ETag claim contract is respected.
    """
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Recovery batch limit must be 1..50")
    now = _utc(now)
    table = table if table is not None else get_jobs_table_client()
    rows = table.query_entities(
        query_filter="PartitionKey eq 'video' and status eq 'queued'",
    )
    results = []
    for row in islice(rows, limit):
        job_id = _id(row["RowKey"])
        try:
            result = dispatch_queued_job(
                job_id, table=table, send=send, now=now,
            )
            results.append({"job_id": job_id, "outcome": result["status"]})
        except DispatchConflict:
            results.append({"job_id": job_id, "outcome": "held"})
        except DispatchUncertain:
            results.append({"job_id": job_id, "outcome": "uncertain"})
    return {"inspected": len(results), "results": results,
            "requires_repeated_sweeps": True}

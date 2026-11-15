"""Durable project alternation for non-billable candidate scheduling.

Each scheduler claims a short-lived lease on one shared Table entity.
No multi-process fairness guarantee exists if callers skip this module.
The lease is not permission to spend or contact a provider; each
selected job requires an independent conditional claim and reservation.
"""

from datetime import datetime, timedelta, timezone
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.data.tables import UpdateMode
from job_scheduler import choose_next

PARTITION = "fal-scheduler-control"
ROW = "global-fairness-v1"


class FairnessBusy(RuntimeError):
    pass


def _now(value):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise FairnessBusy("Timezone-aware clock required")
    return value.astimezone(timezone.utc)


def reserve_scheduler_slot(table, scheduler_id, *, now_time=None, lease_seconds=30):
    """ETag-gated scheduler lease; fail closed on lease ambiguity."""
    from finance_controls import check_id
    check_id(scheduler_id)
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 120:
        raise FairnessBusy("Invalid lease")
    now = _now(now_time)
    try:
        row = table.get_entity(partition_key=PARTITION, row_key=ROW)
    except ResourceExistsError:
        raise FairnessBusy("Scheduler storage unavailable")
    except Exception as exc:
        from azure.core.exceptions import ResourceNotFoundError
        if not isinstance(exc, ResourceNotFoundError):
            raise
        try:
            table.create_entity(entity={
                "PartitionKey": PARTITION, "RowKey": ROW,
                "ownerId": scheduler_id, "lastProjectId": "",
                "leaseUntil": (now + timedelta(seconds=lease_seconds)).isoformat(),
            })
            return {"ownerId": scheduler_id, "lastProjectId": ""}
        except ResourceExistsError as collision:
            raise FairnessBusy("Concurrent scheduler initialization") from collision
    expiry = row.get("leaseUntil")
    try:
        expire_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise FairnessBusy("Ambiguous scheduler lease") from exc
    if expire_time.tzinfo is None or expire_time > now:
        raise FairnessBusy("Another scheduler holds the lease")
    etag = getattr(row, "metadata", {}).get("etag")
    if not etag:
        raise FairnessBusy("Missing scheduler ETag")
    updated = {**dict(row), "ownerId": scheduler_id,
               "leaseUntil": (now + timedelta(seconds=lease_seconds)).isoformat()}
    try:
        table.update_entity(
            entity=updated, mode=UpdateMode.REPLACE,
            etag=etag, match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise FairnessBusy("Competing scheduler") from exc
    return updated


def select_under_lease(held_jobs, lease, *, now_time=None):
    return choose_next(
        held_jobs, last_project_id=lease.get("lastProjectId"),
        now=_now(now_time),
    )


def finish_scheduler_slot(table, scheduler_id, project_id, *,
                          now_time=None):
    from finance_controls import check_id
    check_id(scheduler_id)
    check_id(project_id)
    row = table.get_entity(partition_key=PARTITION, row_key=ROW)
    if row.get("ownerId") != scheduler_id:
        raise FairnessBusy("Scheduler lease ownership changed")
    now = _now(now_time)
    expire_time = datetime.fromisoformat(row["leaseUntil"].replace("Z", "+00:00"))
    if expire_time <= now:
        raise FairnessBusy("Scheduler lease expired; manual review")
    etag = getattr(row, "metadata", {}).get("etag")
    if not etag:
        raise FairnessBusy("Missing scheduler ETag")
    updated = {**dict(row), "lastProjectId": project_id,
               "leaseUntil": now.isoformat(), "ownerId": ""}
    try:
        table.update_entity(
            entity=updated, mode=UpdateMode.REPLACE,
            etag=etag, match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise FairnessBusy("Concurrent scheduler state update") from exc
    return "released"

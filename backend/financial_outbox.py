"""Durable notification/event outbox for financial and hold transitions.

Events are inserted before delivery. Delivery attempts can repeat,
so every external sink MUST deduplicate by event_id.
This is infrastructure glue, not a configured email provider.
"""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.data.tables import UpdateMode

PARTITION = "render-notification-outbox"


class OutboxError(RuntimeError):
    pass


def event_id():
    return str(uuid4())


def queue_notification(table, *, event_type, project_id, target_user_id,
                       job_id=None, payload=None, idempotency_key=None):
    from finance_controls import check_id
    check_id(project_id)
    check_id(target_user_id)
    if job_id is not None:
        check_id(job_id)
    if event_type not in (
        "job_held", "budget_increase_requested", "budget_approved",
        "job_eligible", "job_cancelled", "manual_reconciliation_required",
    ):
        raise OutboxError("Unsupported event type")
    if not isinstance(payload, dict):
        payload = {}
    if payload and not all(
        isinstance(k, str) and isinstance(v, (str, int, bool))
        for k, v in payload.items()
    ):
        raise OutboxError("Only scalar, nonsecret notification fields allowed")
    key = idempotency_key or event_id()
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise OutboxError("Invalid event key")
    row = {
        "PartitionKey": PARTITION, "RowKey": key,
        "eventType": event_type, "projectId": project_id,
        "targetUserId": target_user_id, "jobId": job_id or "",
        "status": "pending",
        "payload": __import__("json").dumps(payload, sort_keys=True),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "attempts": 0,
    }
    try:
        table.create_entity(entity=row)
    except ResourceExistsError as exc:
        raise OutboxError("Event already recorded; do not duplicate") from exc
    return row


def deliver_notification(table, event_key, *, deliver):
    """ETag-lock pending event then call external delivery service.

    Crash after delivery can cause a repeated attempt, so the
    sink must be idempotent on event_key. Failures stay retryable.
    """
    if not callable(deliver):
        raise OutboxError("Delivery sink not configured")
    event = table.get_entity(partition_key=PARTITION, row_key=event_key)
    if event.get("status") != "pending":
        raise OutboxError("Event not pending")
    etag = getattr(event, "metadata", {}).get("etag")
    if not etag:
        raise OutboxError("ETag unavailable")
    claimed = {**dict(event), "status": "delivering",
               "leaseExpiresAt": (datetime.now(timezone.utc) +
                                  timedelta(minutes=5)).isoformat(),
               "attempts": event.get("attempts", 0) + 1}
    try:
        table.update_entity(entity=claimed, mode=UpdateMode.REPLACE,
                            etag=etag,
                            match_condition=MatchConditions.IfNotModified)
    except HttpResponseError as exc:
        raise OutboxError("Concurrent delivery claim") from exc

    # Sink must use event_key as its idempotency key.
    deliver(event_key, dict(event))
    latest = table.get_entity(partition_key=PARTITION, row_key=event_key)
    if latest.get("status") != "delivering":
        raise OutboxError("Delivery status changed; reconcile")
    table.update_entity(
        entity={**dict(latest), "status": "delivered",
                "deliveredAt": datetime.now(timezone.utc).isoformat()},
        mode=UpdateMode.REPLACE,
        etag=latest.metadata["etag"],
        match_condition=MatchConditions.IfNotModified,
    )
    return "delivered"


def pending_notifications(table, limit=50):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise OutboxError("Invalid limit")
    result = table.query_entities(
        query_filter="PartitionKey eq @p and status eq @s",
        parameters={"p": PARTITION, "s": "pending"},
    )
    from itertools import islice
    return list(islice(result, limit))


def diagnose_delivery(table, event_key):
    """Read-only manual recovery of stuck 'delivering' events."""
    return dict(table.get_entity(partition_key=PARTITION, row_key=event_key))


def recover_expired_delivery(table, event_key, *, now_time=None,
                             confirm_idempotent_sink):
    """Requeue a crashed delivery only for a proven idempotent sink.

    A crash after send and before marking delivered may send again.
    Caller must confirm the downstream consumer deduplicates event_key.
    """
    if confirm_idempotent_sink is not True:
        raise OutboxError("Idempotent downstream delivery required")
    event = table.get_entity(partition_key=PARTITION, row_key=event_key)
    if event.get("status") != "delivering":
        raise OutboxError("Event is not awaiting delivery reconciliation")
    expires = event.get("leaseExpiresAt")
    if not isinstance(expires, str):
        raise OutboxError("Missing delivery lease; manual reconciliation")
    try:
        expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutboxError("Invalid delivery lease") from exc
    if expires_at.tzinfo is None:
        raise OutboxError("Lease timezone required")
    now_time = now_time or datetime.now(timezone.utc)
    if now_time.tzinfo is None or now_time < expires_at:
        raise OutboxError("Delivery lease not expired")
    etag = getattr(event, "metadata", {}).get("etag")
    if not etag:
        raise OutboxError("Missing delivery ETag")
    try:
        table.update_entity(
            entity={**dict(event), "status": "pending",
                    "recoveredAt": now_time.isoformat()},
            mode=UpdateMode.REPLACE, etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise OutboxError("Concurrent recovery; no duplicate claim") from exc
    return "pending"


def recover_stalled_notifications(table, *, confirm_idempotent_sink,
                                  limit=50, now_time=None):
    """Bounded scan; only expired leases are reset, not live workers."""
    from itertools import islice
    if confirm_idempotent_sink is not True:
        raise OutboxError("Idempotent sink required for recovery")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise OutboxError("Invalid scan limit")
    rows = table.query_entities(
        query_filter="PartitionKey eq @p and status eq @s",
        parameters={"p": PARTITION, "s": "delivering"},
    )
    summary = {"recovered": 0, "needs_review": 0}
    for item in islice(rows, limit):
        try:
            recover_expired_delivery(
                table, item["RowKey"], now_time=now_time,
                confirm_idempotent_sink=True,
            )
            summary["recovered"] += 1
        except OutboxError:
            summary["needs_review"] += 1
    return summary

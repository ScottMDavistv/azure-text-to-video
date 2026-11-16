"""Durable notification consumer deduplication.

Claim an event ID atomically before applying its effect. If a worker
crashes after the claim, the event remains 'processing' for manual
inspection; no unverified second effect is permitted. A notification
sink must use this guard, not just deduplicate queue messages by ID.
"""

from datetime import datetime, timezone
from azure.core.exceptions import ResourceExistsError, HttpResponseError
from azure.core import MatchConditions
from azure.data.tables import UpdateMode

PARTITION = "notification-inbox-v1"


class InboxBlocked(RuntimeError):
    pass


def apply_notification_once(table, event_id, payload, *, apply):
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        raise InboxBlocked("Invalid event ID")
    if not callable(apply):
        raise InboxBlocked("Trusted notification effect required")
    row = {
        "PartitionKey": PARTITION, "RowKey": event_id,
        "status": "processing",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        table.create_entity(entity=row)
    except ResourceExistsError:
        existing = table.get_entity(partition_key=PARTITION, row_key=event_id)
        if existing.get("status") == "applied":
            return "duplicate_ignored"
        raise InboxBlocked("Ambiguous previously claimed event; manual reconciliation")
    # A failure here leaves processing, preventing unsafe double effects.
    apply(payload)
    latest = table.get_entity(partition_key=PARTITION, row_key=event_id)
    if latest.get("status") != "processing":
        raise InboxBlocked("Notification claim changed")
    etag = getattr(latest, "metadata", {}).get("etag")
    if not etag:
        raise InboxBlocked("Missing inbox ETag")
    try:
        table.update_entity(
            entity={**dict(latest), "status": "applied",
                    "appliedAt": datetime.now(timezone.utc).isoformat()},
            mode=UpdateMode.REPLACE, etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise InboxBlocked("Concurrent inbox update; manual review") from exc
    return "applied"

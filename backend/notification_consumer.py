"""One-message notification consumer with durable event-ID deduplication.

Not automatically launched by any Azure Job. An explicitly configured
Queue client and trusted in-app side-effect handler are required.
Ambiguous previously-claimed notifications are deliberately not acked.
"""

import json
from notification_inbox import apply_notification_once, InboxBlocked


class NotificationConsumeBlocked(RuntimeError):
    pass


def consume_notification_once(queue_client, inbox_table, *, apply,
                              max_bytes=8192):
    if queue_client is None or not callable(getattr(queue_client, "receive_messages", None)):
        raise NotificationConsumeBlocked("Notification queue client required")
    if not callable(apply):
        raise NotificationConsumeBlocked("Trusted notification handler required")
    messages = queue_client.receive_messages(max_messages=1, visibility_timeout=120)
    message = next(iter(messages), None)
    if message is None:
        return "empty"
    content = message.content
    if not isinstance(content, str) or not 1 <= len(content) <= max_bytes:
        raise NotificationConsumeBlocked("Invalid event payload")
    try:
        event = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise NotificationConsumeBlocked("Invalid notification JSON") from exc
    if not isinstance(event, dict):
        raise NotificationConsumeBlocked("Invalid event envelope")
    event_id = event.get("event_id")
    allowed = {"event_id", "event_type", "project_id", "target_user_id", "job_id"}
    if set(event) != allowed or not all(isinstance(x, str) for x in event.values()):
        raise NotificationConsumeBlocked("Invalid notification envelope")
    # A failed or unresolved inbox operation leaves the queue message
    # unacknowledged so operators can investigate; never replay blindly.
    outcome = apply_notification_once(
        inbox_table, event_id, event, apply=apply,
    )
    if outcome not in ("applied", "duplicate_ignored"):
        raise NotificationConsumeBlocked("Unverified notification outcome")
    queue_client.delete_message(message.id, message.pop_receipt)
    return outcome

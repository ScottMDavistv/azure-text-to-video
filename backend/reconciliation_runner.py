"""Explicit one-shot project reconciliation runner.

No background loop, no provider calls and no automatic deployment.
An Azure scheduled job can invoke this after operator configuration.
"""

from finance_controls import check_id
from budget_hold_events import reconcile_hold_notifications
from held_job_coordinator import reconsider_after_budget_approval
from financial_outbox import (pending_notifications, deliver_notification,
                              recover_stalled_notifications)


class ReconciliationDisabled(RuntimeError):
    pass


def run_project_reconciliation(*, enabled=False, project_ids,
                               held_table, finance_table, outbox_table,
                               max_projects=50):
    """Bounded one-shot recovery scan of trusted server-selected projects."""
    if enabled is not True:
        raise ReconciliationDisabled("Reconciliation scan is disabled")
    if isinstance(max_projects, bool) or not isinstance(max_projects, int) or not 1 <= max_projects <= 50:
        raise ValueError("Invalid reconciliation limit")
    if not isinstance(project_ids, (tuple, list)):
        raise ValueError("Server-selected projects required")
    results = []
    for project_id in project_ids[:max_projects]:
        check_id(project_id)
        reconsidered = reconsider_after_budget_approval(
            held_table, finance_table, project_id,
        )
        notices = reconcile_hold_notifications(
            held_table, outbox_table, project_id,
        )
        results.append({
            "project_id": project_id,
            "reconsideration": reconsidered,
            "notifications": notices,
        })
    return {"projects_scanned": len(results), "results": results}


def queue_delivery_sink(queue_client):
    """Return a trusted delivery sink for an in-app notification queue.

    The queue consumer must deduplicate on event_id. The message
    never contains prompt, token, financial account details or secrets.
    """
    import json
    if queue_client is None or not callable(getattr(queue_client, "send_message", None)):
        raise ValueError("Notification queue client required")

    def send(event_id, event):
        payload = {
            "event_id": event_id,
            "event_type": event["eventType"],
            "project_id": event["projectId"],
            "target_user_id": event["targetUserId"],
            "job_id": event.get("jobId", ""),
        }
        queue_client.send_message(json.dumps(payload, sort_keys=True))
    return send


def flush_notifications(*, enabled=False, outbox_table, queue_client,
                        max_events=50):
    if enabled is not True:
        raise ReconciliationDisabled("Notification delivery disabled")
    sink = queue_delivery_sink(queue_client)
    count = 0
    for event in pending_notifications(outbox_table, limit=max_events):
        deliver_notification(outbox_table, event["RowKey"], deliver=sink)
        count += 1
    return {"queued": count}


def recover_notification_leases(*, enabled=False, outbox_table,
                                downstream_deduplicates_by_event_id=False,
                                max_events=50, now_time=None):
    """Run before flush, only with verified consumer event-ID deduplication."""
    if enabled is not True:
        raise ReconciliationDisabled("Notification lease recovery disabled")
    return recover_stalled_notifications(
        outbox_table,
        confirm_idempotent_sink=downstream_deduplicates_by_event_id,
        limit=max_events,
        now_time=now_time,
    )

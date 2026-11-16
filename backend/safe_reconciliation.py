"""Explicit scheduled-work entry points without background side effects.

A real Azure scheduling trigger and authenticated service identity are
required before these functions can be deployed. No FAL POST occurs.
"""

from financial_outbox import pending_notifications, deliver_notification
from held_job_coordinator import reconsider_after_budget_approval
from held_jobs import list_project_holds


def scan_project_holds(held_table, finance_table, project_id):
    """Re-evaluate one project; callers supply authoritative project IDs."""
    return reconsider_after_budget_approval(held_table, finance_table, project_id)


def run_notification_delivery(outbox_table, *, deliver, limit=50):
    """Process bounded pending events, never silently swallow errors."""
    rows = pending_notifications(outbox_table, limit=limit)
    delivered = 0
    for row in rows:
        deliver_notification(outbox_table, row["RowKey"], deliver=deliver)
        delivered += 1
    return {"delivered": delivered, "scanned": len(rows)}


def scan_held_project(held_table, finance_table, project_id):
    """Run one manual/externally scheduled reconciliation pass.

    Scans holds and recomputes capacity. Does not submit to FAL.
    """
    before = sum(
        1 for r in list_project_holds(held_table, project_id)
        if r.get("status") == "held"
    )
    result = scan_project_holds(held_table, finance_table, project_id)
    return {"held_before": before, "reconsideration": result}

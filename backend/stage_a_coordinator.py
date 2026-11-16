"""Stage A non-billable reconciliation entrypoint.

Does not contact FAL, start a worker, or execute provider billing.
For each server-authorized project, reconciles confirmed reservations,
budget eligibility and holds. Suspicious records remain held for review.
"""

from finance_controls import check_id
from held_job_dispatch import reconcile_confirmed_reservation_atomic
from held_jobs import list_project_holds
from reconciliation_runner import run_project_reconciliation, recover_notification_leases, flush_notifications


class StageABlocked(RuntimeError):
    pass


def run_stage_a_once(*, enabled=False, project_ids, held_table,
                     finance_table, outbox_table, queue_client=None,
                     idempotent_consumer_verified=False):
    if enabled is not True:
        raise StageABlocked("Stage A reconciliation disabled")
    if not isinstance(project_ids, (tuple, list)) or len(project_ids) > 50:
        raise StageABlocked("Trusted bounded project list required")
    if queue_client is not None and idempotent_consumer_verified is not True:
        raise StageABlocked('Verified idempotent notification consumer required')
    # Validate the entire list BEFORE any writes.
    for project_id in project_ids:
        check_id(project_id)
    repaired = 0
    blocked = []
    for project_id in project_ids:
        for held in list_project_holds(held_table, project_id):
            if held.get("status") != "reserving":
                continue
            try:
                reconcile_confirmed_reservation_atomic(
                    held_table, finance_table, held["jobId"],
                )
                repaired += 1
            except Exception as exc:
                # Leave financial/provider state unchanged. Caller must
                # review the explicit job ID; no blind retries of payment.
                blocked.append({"job_id": held["jobId"], "reason": type(exc).__name__})
    projects = run_project_reconciliation(
        enabled=True, project_ids=project_ids,
        held_table=held_table, finance_table=finance_table,
        outbox_table=outbox_table,
    )
    notifications = {"status": "not_configured"}
    if queue_client is not None:
        if idempotent_consumer_verified is not True:
            raise StageABlocked("Verified idempotent notification consumer required")
        recovery = recover_notification_leases(
            enabled=True, outbox_table=outbox_table,
            downstream_deduplicates_by_event_id=True,
        )
        sent = flush_notifications(
            enabled=True, outbox_table=outbox_table,
            queue_client=queue_client,
        )
        notifications = {"recovery": recovery, "delivery": sent}
    return {
        "reservations_repaired": repaired,
        "manual_review": blocked,
        "projects": projects,
        "notifications": notifications,
    }

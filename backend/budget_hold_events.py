"""Durable outbox coordination for budget holds/approvals.

Best-effort hooks are NOT enough for atomicity across separate table
partitions. Reconciliation scans may re-enqueue deterministic events
after a crash. Delivery idempotency is required at the sink.
"""

from financial_outbox import queue_notification, OutboxError
from held_job_coordinator import reconsider_after_budget_approval
from held_jobs import list_project_holds


def emit_hold_notice(outbox_table, hold):
    return queue_notification(
        outbox_table,
        event_type="job_held",
        project_id=hold["projectId"],
        target_user_id=hold["ownerId"],
        job_id=hold["jobId"],
        payload={"reason": hold["holdReason"],
                 "estimatedCents": hold["estimatedCents"]},
        idempotency_key="hold:" + hold["jobId"],
    )


def emit_approval_notice(outbox_table, *, request_id, project_id, owner_id):
    return queue_notification(
        outbox_table,
        event_type="budget_approved",
        project_id=project_id,
        target_user_id=owner_id,
        payload={"requestId": request_id},
        idempotency_key="approved:" + request_id,
    )


def on_budget_approval(held_table, finance_table, outbox_table,
                       *, request_id, project_id, owner_id):
    """Requires already-committed approved request, verified by caller.

    Production event consumer must read the durable approval record
    itself before invoking. Reconciliation handles crash-after-approval.
    """
    from finance_controls import load, scope_key
    request = load(finance_table, 'increase:' + request_id)
    if (request.get('status') != 'approved' or
            request.get('scope') != scope_key(project_id) or
            request.get('requesterId') != owner_id):
        raise OutboxError('Budget approval not verified in durable ledger')
    result = reconsider_after_budget_approval(
        held_table, finance_table, project_id,
    )
    try:
        emit_approval_notice(
            outbox_table, request_id=request_id,
            project_id=project_id, owner_id=owner_id,
        )
    except OutboxError as exc:
        if "already recorded" not in str(exc):
            raise
    return result


def reconcile_hold_notifications(held_table, outbox_table, project_id):
    """Replay missing hold notices using job IDs as idempotency keys."""
    created = 0
    for hold in list_project_holds(held_table, project_id):
        try:
            emit_hold_notice(outbox_table, hold)
            created += 1
        except OutboxError as exc:
            if "already recorded" not in str(exc):
                raise
    return {"new_notifications": created}

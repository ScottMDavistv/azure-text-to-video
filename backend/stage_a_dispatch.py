"""Distributed fairness and conditional spending-reservation orchestration.

Never contacts a provider or queues live paid rendering. The leased
scheduler slot is held until a candidate is conditionally claimed
and its financial reservation has been attempted. On uncertainty,
the lease expires but the job remains held for reconciliation.
"""

from distributed_fairness import (
    reserve_scheduler_slot, select_under_lease, finish_scheduler_slot,
)
from held_jobs import list_project_holds
from held_job_dispatch import dispatch_candidate
from fal_budget_guard import has_capacity
from finance_controls import check_id


class StageADispatchBlocked(RuntimeError):
    pass


def dispatch_fair_once(*, enabled=False, scheduler_table, held_table,
                       finance_table, project_ids, scheduler_id):
    if enabled is not True:
        raise StageADispatchBlocked("Non-billable dispatch disabled")
    if not isinstance(project_ids, (list, tuple)) or not 1 <= len(project_ids) <= 50:
        raise StageADispatchBlocked("Trusted nonempty project list required")
    check_id(scheduler_id)
    for pid in project_ids:
        check_id(pid)
    lease = reserve_scheduler_slot(scheduler_table, scheduler_id)
    available = []
    for project_id in project_ids:
        for job in list_project_holds(held_table, project_id):
            if job.get("status") not in ("held", "eligible"):
                continue
            if has_capacity(finance_table, project_id, job["estimatedCents"]):
                available.append(job)
    selected = select_under_lease(available, lease)
    if selected is None:
        # Lease expires on its own. No capacity or paid submission.
        return {"status": "no_eligible_jobs"}
    # If this raises, do not advance fairness or retry the reservation:
    # the financial outcome might already have committed.
    reservation = dispatch_candidate(
        held_table, finance_table, selected["jobId"],
    )
    finish_scheduler_slot(
        scheduler_table, scheduler_id, selected["projectId"],
    )
    return {
        "status": "reserved", "job_id": selected["jobId"],
        "project_id": selected["projectId"], "reservation": reservation,
    }

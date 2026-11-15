"""Opt-in, non-rendering coordination for budget-held video jobs.

Integrates candidate scheduling, available capacity checks, and atomic
spending reservations. Does not run on a timer and never contacts FAL.
"""

from finance_controls import FinanceDenied
from fal_budget_guard import has_capacity
from held_job_dispatch import dispatch_candidate
from held_jobs import list_project_holds, reconsider_project_holds
from job_scheduler import choose_next


def reconsider_after_budget_approval(held_table, finance_table, project_id):
    """Call after confirmed approval, or during scheduled repair scans.

    This is the integration hook; the production outbox/scheduler is
    not yet wired to invoke it automatically.
    """
    return reconsider_project_holds(
        held_table, project_id,
        verify_budget=lambda pid, cents: has_capacity(finance_table, pid, cents),
    )


def dispatch_next_project_job(held_table, finance_table, project_id, *,
                              last_project_id=None):
    """Claim at most one job with available budget; no live rendering.

    Availability is advisory. The durable reserve() transaction is
    authoritative and can fail under concurrency. Uncertain outcomes
    require reconciliation and must not be automatically retried.
    """
    rows = [
        r for r in list_project_holds(held_table, project_id)
        if r.get("status") in ("held", "eligible")
    ]
    candidates = []
    for row in rows:
        if has_capacity(finance_table, row["projectId"], row["estimatedCents"]):
            candidates.append(row)
    selected = choose_next(candidates, last_project_id=last_project_id)
    if selected is None:
        return {"status": "no_eligible_jobs"}
    reservation = dispatch_candidate(
        held_table, finance_table, selected["jobId"]
    )
    return {
        "status": "reserved",
        "job_id": selected["jobId"],
        "project_id": selected["projectId"],
        "reservation": reservation,
    }

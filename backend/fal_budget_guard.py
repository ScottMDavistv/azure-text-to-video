"""Trusted read-only checks binding FAL submission to financial ledger.

The caller must use the exact server-resolved job project ID.
No client-supplied project ID or verification boolean is trusted.
"""

from finance_controls import FinanceDenied, check_id, check_money, load, scope_key


def verify_job_reservation(table, job_id, project_id):
    """Confirm an existing charged-capacity reservation for this exact job.

    Financial ledger writes happen in the atomic reserve() transaction.
    This is a second read-only check immediately before submission.
    """
    check_id(job_id)
    check_id(project_id)
    record = load(table, "reservation:" + job_id)
    if record.get("state") != "reserved":
        return False
    if record.get("projectId") != project_id:
        return False
    amount = check_money(record.get("reservedCents"))
    for scope in (scope_key(project_id), "global:fal"):
        limit = load(table, scope)
        if (
            type(limit.get("reservedCents")) is not int
            or limit["reservedCents"] < amount
            or type(limit.get("ceilingCents")) is not int
            or type(limit.get("spentCents")) is not int
            or limit["spentCents"] + limit["reservedCents"] > limit["ceilingCents"]
        ):
            raise FinanceDenied("Inconsistent financial ledger")
    return True


def make_reservation_verifier(table, *, project_id):
    """Server-side callback for fal_safe_submission.submit_once."""
    check_id(project_id)
    return lambda job_id: verify_job_reservation(table, job_id, project_id)


def has_capacity(table, project_id, estimated_cents):
    """Advisory check for reconsideration; reserve() is authoritative."""
    check_id(project_id)
    amount = check_money(estimated_cents)
    for scope in (scope_key(project_id), "global:fal"):
        limit = load(table, scope)
        available = limit["ceilingCents"] - limit["spentCents"] - limit["reservedCents"]
        if available < amount:
            return False
    return True

"""Fail-closed dispatcher for budget-held jobs.

This module does not start a worker and never contacts FAL.
Claims are conditional, spending reservations are durable, and
uncertain claims are NOT automatically released.
"""

from datetime import datetime, timezone

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from finance_controls import FinanceDenied, reserve
from held_jobs import HeldJobDenied, PARTITION


def _change(table, existing, status, **extra):
    """ETag-protected transition; caller must re-read on conflict."""
    if not getattr(existing, "metadata", {}).get("etag"):
        raise HeldJobDenied("Missing ETag")
    new = {**dict(existing), "status": status,
           "updatedAt": datetime.now(timezone.utc).isoformat(), **extra}
    try:
        table.update_entity(
            entity=new, mode=UpdateMode.REPLACE,
            etag=existing.metadata["etag"],
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise HeldJobDenied("Concurrent held-job change") from exc
    return new


def claim_for_reservation(held_table, job_id):
    """Atomically held/eligible -> reserving; no provider network call."""
    current = held_table.get_entity(
        partition_key=PARTITION, row_key=job_id
    )
    if current["status"] not in {"held", "eligible"}:
        raise HeldJobDenied("Job cannot be claimed")
    return _change(held_table, current, "reserving")


def reserve_claimed_job(held_table, finance_table, job_id):
    """Reserve both budgets for a claimed job; no provider submission.

    A failed financial reservation leaves the job 'reserving'.
    This is deliberate: a transaction timeout may hide success.
    Reconcile financial reservation by job ID before changing
    or retrying a claim; NEVER automatically reserve again.
    """
    row = held_table.get_entity(
        partition_key=PARTITION, row_key=job_id
    )
    if row["status"] != "reserving":
        raise HeldJobDenied("Job is not reserving")
    reservation = reserve(
        finance_table, row["projectId"], job_id, row["estimatedCents"]
    )
    # An ETag failure here leaves a durable reservation and the
    # row in reserving. Reconcile rather than duplicate a claim.
    _change(held_table, row, "reserved")
    return reservation


def dispatch_candidate(held_table, finance_table, job_id):
    """One attempt: claim, then reserve. Does not contact FAL."""
    claim_for_reservation(held_table, job_id)
    return reserve_claimed_job(held_table, finance_table, job_id)


def reconcile_reserving(held_table, finance_table, job_id):
    """Read-only diagnosis, never a new financial write.

    Return explicit evidence for an operator. No automatic
    transition on ambiguous/missing financial data.
    """
    from finance_controls import load
    current = held_table.get_entity(
        partition_key=PARTITION, row_key=job_id
    )
    if current["status"] != "reserving":
        raise HeldJobDenied("Not an unresolved reservation")
    try:
        reservation = load(finance_table, "reservation:" + job_id)
    except FinanceDenied:
        return "manual_review_no_confirmed_reservation"
    if (
        reservation.get("projectId") == current["projectId"]
        and reservation.get("state") == "reserved"
        and reservation.get("reservedCents") == current["estimatedCents"]
    ):
        return "manual_review_reservation_exists"
    return "manual_review_inconsistent_finance"

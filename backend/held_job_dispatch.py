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


def reconcile_confirmed_reservation(held_table, finance_table, job_id,
                                    *, record_audit):
    """Repair ONLY a confirmed reservation with a conditional state write.

    Never calls reserve() or FAL. Missing or inconsistent evidence
    remains unresolved. Operator/scan supplies durable audit callback.
    """
    from finance_controls import load, check_id
    check_id(job_id)
    if not callable(record_audit):
        raise HeldJobDenied("Durable reconciliation audit required")
    held = held_table.get_entity(partition_key=PARTITION, row_key=job_id)
    if held.get("status") == "reserved":
        record = load(finance_table, "reservation:" + job_id)
        if (record.get("state") == "reserved" and
                record.get("projectId") == held.get("projectId") and
                record.get("reservedCents") == held.get("estimatedCents")):
            return "already_reserved"
        raise HeldJobDenied("Reserved hold contradicts financial ledger")
    if held.get("status") != "reserving":
        raise HeldJobDenied("Job not awaiting reservation reconciliation")
    try:
        reservation = load(finance_table, "reservation:" + job_id)
    except FinanceDenied as exc:
        raise HeldJobDenied("No confirmed reservation; manual review required") from exc
    if (reservation.get("state") != "reserved" or
            reservation.get("projectId") != held.get("projectId") or
            reservation.get("reservedCents") != held.get("estimatedCents")):
        raise HeldJobDenied("Inconsistent reservation; manual review required")
    # Write an idempotent audit event BEFORE flipping to reserved.
    # The audit sink must reject duplicates by a stable event key.
    if record_audit("reservation_reconciled:" + job_id, job_id) is not True:
        raise HeldJobDenied("Durable audit acknowledgement required")
    _change(held_table, held, "reserved",
            reconciliation="confirmed_finance_reservation")
    return "reserved"


def reconcile_confirmed_reservation_atomic(held_table, finance_table, job_id):
    """Atomically audit and confirm a *pre-existing* financial reservation.

    Both the held job row and the audit row use PARTITION, so an Azure
    Table batch can ETag-update the job and create its audit in one
    transaction. Never reserves funds or contacts the provider.
    """
    from finance_controls import load, check_id
    check_id(job_id)
    held = held_table.get_entity(partition_key=PARTITION, row_key=job_id)
    try:
        reservation = load(finance_table, "reservation:" + job_id)
    except FinanceDenied as exc:
        raise HeldJobDenied("Financial reservation not confirmed") from exc
    if (reservation.get("state") != "reserved" or
            reservation.get("projectId") != held.get("projectId") or
            reservation.get("reservedCents") != held.get("estimatedCents")):
        raise HeldJobDenied("Financial reservation does not match held job")
    if held.get("status") == "reserved":
        # A prior committed atomic update is sufficient proof of state
        # transition, but verify the audit record was persisted as well.
        audit = held_table.get_entity(
            partition_key=PARTITION, row_key="audit:reserve:" + job_id,
        )
        if audit.get("eventType") != "reservation_reconciled":
            raise HeldJobDenied("Reserved state missing reconciliation audit")
        return "already_reserved"
    if held.get("status") != "reserving":
        raise HeldJobDenied("Hold not in reconcilable state")
    etag = getattr(held, "metadata", {}).get("etag")
    if not etag:
        raise HeldJobDenied("Held job concurrency ETag required")
    timestamp = datetime.now(timezone.utc).isoformat()
    updated = {
        **dict(held), "status": "reserved",
        "reconciliation": "confirmed_finance_reservation",
        "updatedAt": timestamp,
    }
    audit = {
        "PartitionKey": PARTITION,
        "RowKey": "audit:reserve:" + job_id,
        "eventType": "reservation_reconciled",
        "jobId": job_id,
        "projectId": held["projectId"],
        "amountCents": held["estimatedCents"],
        "createdAt": timestamp,
    }
    try:
        held_table.submit_transaction([
            ("update", updated, {
                "mode": UpdateMode.REPLACE,
                "etag": etag,
                "match_condition": MatchConditions.IfNotModified,
            }),
            ("create", audit),
        ])
    except HttpResponseError as exc:
        raise HeldJobDenied("Conditional reconciliation failed; reread evidence") from exc
    return "reserved"

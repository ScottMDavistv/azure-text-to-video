"""Experimental F-03/F-04 financial receipt and recovery primitives.

Development only. No FAL integration, no HTTP routes, no Azure deployment.
The application must provide an *independent trusted* verifier for both
provider receipts and conclusive evidence that submission never occurred.
"""
import hashlib
import unicodedata
from uuid import uuid4

from finance_controls import (
    PARTITION, FinanceDenied, audit, check_id, check_money, commit,
    create, load, now, replace, require_admin, scope_key,
)
from provider_submission_ledger import (
    PARTITION as SUBMISSION_PARTITION, read_intent,
)


def receipt_key(provider_name, receipt_id):
    """Deduplication identity is provider-scoped, not job-scoped."""
    if (not isinstance(provider_name, str) or provider_name != "fal"
            or not isinstance(receipt_id, str)
            or not 1 <= len(receipt_id) <= 256):
        raise FinanceDenied("Valid provider and receipt identity required")
    if receipt_id != receipt_id.strip() or any(
        unicodedata.category(char).startswith("C") for char in receipt_id
    ):
        raise FinanceDenied("Noncanonical provider receipt identity")
    canonical_id = unicodedata.normalize("NFKC", receipt_id).casefold()
    if not canonical_id or canonical_id != canonical_id.strip():
        raise FinanceDenied("Noncanonical provider receipt identity")
    digest = hashlib.sha256(
        (provider_name + ":" + canonical_id).encode("utf-8")
    ).hexdigest()
    return "receipt:" + digest


def provider_request_key(provider_name, request_id):
    """Provider-scoped unique submission identity, across all jobs."""
    if (provider_name != "fal" or not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 256
            or request_id != request_id.strip()
            or any(unicodedata.category(c).startswith("C") for c in request_id)):
        raise FinanceDenied("Valid provider request identity required")
    canonical_id = unicodedata.normalize("NFKC", request_id).casefold()
    if not canonical_id or canonical_id != canonical_id.strip() or any(
        unicodedata.category(c).startswith("C") for c in canonical_id
    ):
        raise FinanceDenied("Noncanonical provider request identity")
    return "provider-request:" + hashlib.sha256(
        (provider_name + ":" + canonical_id).encode("utf-8")
    ).hexdigest()


def require_durable_submission(submission_table, job_id, provider_request_id):
    """Bind a claimed provider request to this job's durable submission.

    Reads the existing write-once submission intent (provider_submission_
    ledger) for ``job_id`` and requires state ``submitted`` with exactly
    the claimed provider request ID. Any missing, unreadable, unresolved,
    foreign or mismatched record fails closed. Read-only; no new schema.
    """
    if submission_table is None:
        raise FinanceDenied("Durable provider submission ledger required")
    try:
        intent = read_intent(submission_table, job_id)
    except Exception as exc:
        raise FinanceDenied("Durable provider submission unavailable") from exc
    if (not hasattr(intent, "get")
            or intent.get("PartitionKey") != SUBMISSION_PARTITION
            or intent.get("RowKey") != job_id):
        raise FinanceDenied("Durable provider submission does not belong to job")
    if intent.get("state") != "submitted":
        raise FinanceDenied("Provider submission is not durably recorded")
    durable_request_id = intent.get("providerRequestId")
    if (not isinstance(durable_request_id, str) or not durable_request_id
            or durable_request_id != provider_request_id):
        raise FinanceDenied(
            "Provider request does not match the job's durable submission")
    return durable_request_id


def settle_unique_receipt(table, *, job_id, actual_cents,
                          provider_request_id, receipt_id,
                          provider_receipt, verify_provider_receipt,
                          submission_table=None):
    """Atomically deduplicate a verified charge and update both ceilings.

    ``table`` is the finance ledger; ``submission_table`` is the durable
    provider submission ledger. Before the verifier runs and before any
    finance mutation, the claimed provider request ID must exactly equal
    the request ID durably recorded for this job (L-2); a missing
    submission ledger fails closed.

    The trusted verifier MUST still cross-check the provider's
    authenticated billing record, including internal job, provider
    request ID, currency, receipt ID and cost. The provider-scoped
    request guard row and receipt row remain in the single-partition
    atomic batch, so a duplicate request or receipt across jobs still
    aborts the whole transaction.
    This module does not fetch FAL billing and is NOT activation-ready.
    """
    check_id(job_id)
    actual = check_money(actual_cents, allow_zero=True)
    if (not isinstance(provider_request_id, str)
            or not 1 <= len(provider_request_id) <= 256):
        raise FinanceDenied("Provider request ID required")
    key = receipt_key("fal", receipt_id)
    request_key = provider_request_key("fal", provider_request_id)
    require_durable_submission(submission_table, job_id, provider_request_id)
    if provider_receipt is None or not callable(verify_provider_receipt):
        raise FinanceDenied("Authenticated provider billing evidence required")
    try:
        valid = verify_provider_receipt(
            job_id, provider_request_id, receipt_id, actual, provider_receipt
        )
    except Exception as exc:
        raise FinanceDenied("Provider billing verification unavailable") from exc
    if valid is not True:
        raise FinanceDenied("Provider billing evidence rejected")

    reservation = load(table, "reservation:" + job_id)
    if reservation["state"] != "reserved":
        raise FinanceDenied("Reservation is not open")
    if actual > reservation["reservedCents"]:
        raise FinanceDenied("Overrun requires independent reconciliation")
    project = load(table, scope_key(reservation["projectId"]))
    global_limit = load(table, "global:fal")
    amount = reservation["reservedCents"]
    for limit in (project, global_limit):
        if limit["reservedCents"] < amount:
            raise FinanceDenied("Inconsistent reserved funds")

    op_id = str(uuid4())
    commit(table, [
        replace(project, reservedCents=project["reservedCents"] - amount,
                spentCents=project["spentCents"] + actual),
        replace(global_limit, reservedCents=global_limit["reservedCents"] - amount,
                spentCents=global_limit["spentCents"] + actual),
        replace(reservation, state="settled", actualCents=actual,
                providerRequestId=provider_request_id, receiptKey=key,
                settledAt=now()),
        create({"PartitionKey": PARTITION, "RowKey": request_key,
                "jobId": job_id, "provider": "fal",
                "providerRequestId": provider_request_id,
                "receiptKey": key, "createdAt": now()}),
        create({"PartitionKey": PARTITION, "RowKey": key, "jobId": job_id,
                "provider": "fal", "providerRequestId": provider_request_id,
                "receiptId": receipt_id, "actualCents": actual,
                "createdAt": now()}),
        create(audit("verified_receipt_settled", op_id=op_id, jobId=job_id,
                     receiptKey=key, amountCents=actual)),
    ], op_id=op_id)
    return {"job_id": job_id, "state": "settled",
            "receipt_key": key, "actual_cents": actual}


def inspect_reservation(table, job_id):
    """Read-only recovery diagnosis; never guesses provider billing state."""
    check_id(job_id)
    r = load(table, "reservation:" + job_id)
    return {"job_id": job_id, "project_id": r["projectId"],
            "state": r["state"], "reserved_cents": r["reservedCents"],
            "actual_cents": r.get("actualCents", 0),
            "receipt_key": r.get("receiptKey")}


def request_unsubmitted_release(table, *, job_id, first_admin,
                                admin_lookup, release_id):
    """Record first independent admin authorization; no funds move."""
    check_id(job_id)
    check_id(release_id)
    require_admin(first_admin, admin_lookup)
    reservation = load(table, "reservation:" + job_id)
    if reservation["state"] != "reserved":
        raise FinanceDenied("Only open reservations can be reviewed")
    record = {"PartitionKey": PARTITION, "RowKey": "release:" + release_id,
              "jobId": job_id, "firstApproverId": first_admin.user_id,
              "status": "pending", "createdAt": now()}
    # Only one release request may exist for each job; the guard row
    # and the approval intent are created in the same atomic batch.
    op_id = str(uuid4())
    commit(table, [create(record),
                   create({"PartitionKey": PARTITION,
                           "RowKey": "release-job:" + job_id,
                           "releaseId": release_id, "createdAt": now()}),
                   create(audit("release_requested", op_id=op_id, jobId=job_id,
                                actorId=first_admin.user_id,
                                releaseId=release_id))], op_id=op_id)
    return {"release_id": release_id, "state": "pending"}


def approve_unsubmitted_release(table, *, release_id, second_admin,
                                admin_lookup, no_submission_evidence,
                                verify_never_submitted):
    """Release only after two different admins and conclusive source proof.

    A successful provider response, missing local job record, or timeout
    is NOT proof that no submission/charge occurred. Verifier must consult
    durable provider/submission state and authorized billing evidence.
    """
    check_id(release_id)
    require_admin(second_admin, admin_lookup)
    approval = load(table, "release:" + release_id)
    if approval["status"] != "pending":
        raise FinanceDenied("Release approval already closed")
    if approval["firstApproverId"] == second_admin.user_id:
        raise FinanceDenied("Distinct second administrator required")
    # The initial approver must still be authorized at the point funds
    # move; a revoked or removed administrator cannot contribute approval.
    if admin_lookup(approval["firstApproverId"]) is not True:
        raise FinanceDenied("Original administrator approval was revoked")
    job_id = check_id(approval["jobId"])
    if no_submission_evidence is None or not callable(verify_never_submitted):
        raise FinanceDenied("Trusted no-submission proof required")
    try:
        valid = verify_never_submitted(job_id, no_submission_evidence)
    except Exception as exc:
        raise FinanceDenied("No-submission verifier unavailable") from exc
    if valid is not True:
        raise FinanceDenied("No-submission proof was not confirmed")

    reservation = load(table, "reservation:" + job_id)
    if reservation["state"] != "reserved":
        raise FinanceDenied("Reservation already closed")
    amount = reservation["reservedCents"]
    project = load(table, scope_key(reservation["projectId"]))
    global_limit = load(table, "global:fal")
    for limit in (project, global_limit):
        if limit["reservedCents"] < amount:
            raise FinanceDenied("Inconsistent held funds")

    op_id = str(uuid4())
    commit(table, [
        replace(project, reservedCents=project["reservedCents"] - amount),
        replace(global_limit, reservedCents=global_limit["reservedCents"] - amount),
        replace(reservation, state="released_unsubmitted",
                actualCents=0, releasedAt=now()),
        replace(approval, status="approved", secondApproverId=second_admin.user_id,
                approvedAt=now()),
        create(audit("release_approved", op_id=op_id, jobId=job_id, releaseId=release_id,
                     firstApproverId=approval["firstApproverId"],
                     secondApproverId=second_admin.user_id)),
    ], op_id=op_id)
    return {"job_id": job_id, "state": "released_unsubmitted"}

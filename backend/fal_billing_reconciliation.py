"""Fail-closed provider billing reconciliation.

No Fal billing API integration is configured. Caller must fetch the
charge record from a trusted service and verify it for request ID.
Any uncertain or over-estimate charge stays reserved for review.
"""

from finance_controls import check_id, check_money, load, FinanceDenied
from provider_submission_ledger import read_intent
from budgeted_fal_execution import settle_verified_charge, ExecutionBlocked


class BillingReviewRequired(RuntimeError):
    pass


def reconcile_verified_fal_charge(
    finance_table, submission_table, job_id, *,
    actual_cents, provider_receipt, verify_provider_receipt,
):
    """Confirm exact provider request and ledger before settlement.

    verify_provider_receipt(job_id, request_id, actual_cents, receipt)
    MUST validate independently authenticated billing and durable
    request/job linkage, never client-submitted charge metadata.
    """
    check_id(job_id)
    check_money(actual_cents, allow_zero=True)
    submission = read_intent(submission_table, job_id)
    request_id = submission.get("providerRequestId")
    if submission.get("state") != "submitted" or not request_id:
        raise BillingReviewRequired("Provider request is unresolved")
    reservation = load(finance_table, "reservation:" + job_id)
    if reservation.get("state") != "reserved":
        raise BillingReviewRequired("Financial reservation is unresolved")
    if actual_cents > reservation.get("reservedCents", -1):
        raise BillingReviewRequired(
            "Provider charge exceeds reservation; manual correction required"
        )
    if not callable(verify_provider_receipt) or provider_receipt is None:
        raise BillingReviewRequired("Trusted billing evidence required")
    # Precheck before invoking downstream adapters; the hardened ledger
    # must separately reverify at commit time so adapter mocks cannot
    # bypass its authenticated-receipt boundary.
    try:
        accepted = verify_provider_receipt(
            job_id, request_id, actual_cents, provider_receipt
        )
    except Exception as exc:
        raise BillingReviewRequired("Billing verification failed") from exc
    if accepted is not True:
        raise BillingReviewRequired("Unverified provider billing receipt")
    # Do not replace the real verifier with an ID/amount equality lambda.
    # The underlying finance_controls.settle will invoke this verifier
    # at its fail-closed ledger boundary.
    return settle_verified_charge(
        finance_table=finance_table, job_id=job_id,
        charge_cents=actual_cents, provider_request_id=request_id,
        provider_receipt=provider_receipt,
        verify_provider_receipt=verify_provider_receipt,
        submission_table=submission_table,
    )

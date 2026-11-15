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

    verify_provider_receipt(receipt, request_id, actual_cents) must
    validate billing against an authenticated provider API, not
    client-submitted charge metadata.
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
    if not callable(verify_provider_receipt):
        raise BillingReviewRequired("Trusted billing verifier required")
    if verify_provider_receipt(
        provider_receipt, request_id, actual_cents,
    ) is not True:
        raise BillingReviewRequired("Unverified provider billing receipt")
    return settle_verified_charge(
        finance_table=finance_table,
        job_id=job_id,
        charge_cents=actual_cents,
        verified_billing=lambda claimed_id, cents: (
            claimed_id == job_id and cents == actual_cents
        ),
    )

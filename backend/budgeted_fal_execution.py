"""Explicit opt-in, fail-closed FAL execution adapter.

Never used by default worker_service or worker_cli.
Never settles funds without trusted verified provider billing.
"""

from uuid import UUID

from fal_budget_guard import make_reservation_verifier
from fal_safe_submission import submit_and_wait_guarded
from finance_controls import FinanceDenied, check_id


class ExecutionBlocked(RuntimeError):
    pass


def run_reserved_fal_job(*, enabled, job_id, jobs_table, finance_table,
                         submission_table, client,
                         authorize_job, verified_actual_charge=None):
    """Submit exactly once for an authorized job with confirmed reservation.

    Authorization callback MUST come from trusted server-side identity
    and project membership resolution. It must not be HTTP-client input.

    Leaving funds reserved without verified billing is deliberate.
    No default application code calls this function.
    """
    if enabled is not True:
        raise ExecutionBlocked("Live FAL execution is disabled")
    check_id(job_id)
    if not callable(authorize_job):
        raise ExecutionBlocked("Trusted job authorization required")
    job = jobs_table.get_entity(partition_key="video", row_key=job_id)
    project_id = check_id(job.get("projectId"))
    if job.get("status") != "processing":
        raise ExecutionBlocked("Job must be exclusively claimed by worker")
    if authorize_job(job_id, project_id) is not True:
        raise ExecutionBlocked("Job authorization denied")
    prompt = job.get("prompt")
    verifier = make_reservation_verifier(finance_table, project_id=project_id)
    result = submit_and_wait_guarded(
        submission_table, job_id, prompt, client,
        verify_reservation=verifier,
    )
    # Rendering and verified billing reconciliation are distinct steps.
    # A completed provider response is not evidence of its actual charge.
    return {
        "job_id": job_id,
        "project_id": project_id,
        "video_url": result["video_url"],
        "billing_status": "awaiting_verified_charge",
        "reservation_state": "reserved",
    }


def settle_verified_charge(*, finance_table, job_id, charge_cents,
                           provider_request_id, provider_receipt,
                           verify_provider_receipt):
    """Bridge hardened Bulk 02's receipt-aware settlement boundary.

    The verifier MUST use independently authenticated provider billing
    records bound to the server-side provider submission intent, not UI
    claims. Callback is backend-injected only; there is no live route.
    """
    from finance_controls import settle, check_money

    check_id(job_id)
    charge_cents = check_money(charge_cents, allow_zero=True)
    if (not isinstance(provider_request_id, str)
            or not provider_request_id.strip()
            or provider_receipt is None
            or not callable(verify_provider_receipt)):
        raise ExecutionBlocked("Authenticated provider billing evidence required")

    def verify_for_ledger(claimed_job_id, claimed_cents, claimed_receipt):
        if (claimed_job_id != job_id or claimed_cents != charge_cents
                or claimed_receipt is not provider_receipt):
            return False
        return verify_provider_receipt(
            job_id, provider_request_id, charge_cents, claimed_receipt
        ) is True

    return settle(
        finance_table, job_id, charge_cents,
        provider_receipt=provider_receipt,
        verify_provider_receipt=verify_for_ledger,
    )

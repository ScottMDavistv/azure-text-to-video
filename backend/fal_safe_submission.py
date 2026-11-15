"""Fail-closed FAL submission orchestration primitives.

Not connected to the live worker until budget reservation and
trusted job/project authorization are integrated and verified.
No network calls happen on import.
"""

from collections.abc import Callable

from fal_polling import poll_until_complete, get_completed_video
from provider_submission_ledger import (
    SubmissionBlocked,
    claim_intent,
    create_intent,
    record_provider_request_id,
)
from video_provider import validate_prompt


class PaidSubmissionBlocked(SubmissionBlocked):
    """No provider submission may occur without approved preconditions."""


def submit_once(table, job_id, prompt, client, *, verify_reservation):
    """Persist intent before contacting FAL; never retry ambiguous submissions.

    verify_reservation must be a trusted, server-side callback that
    checks a durable project+global reservation for this precise job.
    This callback is NOT supplied by an HTTP client.

    A crash/network timeout after claim can leave 'submitting' permanently
    unresolved; manual reconciliation is required, never auto-resubmission.
    """
    if not callable(verify_reservation):
        raise PaidSubmissionBlocked("Durable reservation verification required")
    if client is None or not callable(getattr(client, "submit", None)):
        raise PaidSubmissionBlocked("FAL client required")
    prompt = validate_prompt(prompt)

    # A strict True response is required. An exception propagates
    # without creating an intent or contacting the provider.
    if verify_reservation(job_id) is not True:
        raise PaidSubmissionBlocked("Verified reserved capacity required")

    create_intent(table, job_id)
    claim_intent(table, job_id)

    # Every failure below is potentially billable and MUST leave the
    # intent non-retriable until provider reconciliation.
    response = client.submit(prompt)
    if not isinstance(response, dict):
        raise PaidSubmissionBlocked("Unexpected FAL submission response")

    request_id = response.get("request_id")
    record_provider_request_id(
        table, job_id, request_id,
        status_url=response.get("status_url"),
        response_url=response.get("response_url"),
    )
    return response


def submit_and_wait_guarded(
    table,
    job_id,
    prompt,
    client,
    *,
    verify_reservation,
    timeout_seconds=900,
    poll_interval_seconds=10,
):
    """Guarded submission followed by non-submitting status polling.

    Only explicitly opt-in application code can call this function.
    Do not use it until the reservation callback and Azure ETag tests
    have been verified in a live-equivalent environment.
    """
    submission = submit_once(
        table, job_id, prompt, client,
        verify_reservation=verify_reservation,
    )
    completion = poll_until_complete(
        client,
        submission,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return get_completed_video(client, completion)

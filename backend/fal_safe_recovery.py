"""Resume polling only previously submitted FAL requests.

Never submits a new paid FAL request. Missing/mismatched metadata
requires operator reconciliation, not a retry of the POST.
"""

from fal_polling import poll_until_complete, get_completed_video
from fal_provider import validate_queue_url
from provider_submission_ledger import SubmissionBlocked, read_intent


def poll_existing_submission(table, job_id, client, *,
                             timeout_seconds=900, poll_interval_seconds=10):
    if client is None:
        raise SubmissionBlocked("Provider client required")
    intent = read_intent(table, job_id)
    if intent.get("state") != "submitted":
        raise SubmissionBlocked("Provider submission is not safely resumable")
    request_id = intent.get("providerRequestId")
    status_url = intent.get("statusUrl")
    response_url = intent.get("responseUrl")
    if not (request_id and status_url and response_url):
        raise SubmissionBlocked("Polling metadata missing; manual reconciliation required")
    validate_queue_url(status_url)
    validate_queue_url(response_url)
    completion = poll_until_complete(
        client,
        {"request_id": request_id,
         "status_url": status_url,
         "response_url": response_url},
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return get_completed_video(client, completion)

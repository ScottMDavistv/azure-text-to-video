"""Post-provider settlement and finalization, without a second FAL POST.

The authoritative charge must already have been verified and settled.
An ETag transition avoids clobbering a concurrent cancellation/recovery.
"""

from datetime import datetime, timezone
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from finance_controls import check_id, load
from provider_submission_ledger import read_intent


class FinalizationBlocked(RuntimeError):
    pass


def finalize_paid_job(*, job_id, jobs_table, finance_table, submission_table):
    """Idempotent completion only after both provider and financial evidence."""
    check_id(job_id)
    job = jobs_table.get_entity(partition_key="video", row_key=job_id)
    if job.get("status") == "completed":
        if job.get("billingStatus") == "settled" and job.get("blobName"):
            return {"job_id": job_id, "status": "completed", "replayed": True}
        raise FinalizationBlocked("Inconsistent completed job")
    if job.get("status") != "processing" or not job.get("blobName"):
        raise FinalizationBlocked("Video artifact has not been durably recorded")
    if job.get("billingStatus") != "awaiting_verified_charge":
        raise FinalizationBlocked("Job is not waiting for verified billing")
    project_id = check_id(job.get("projectId"))
    intent = read_intent(submission_table, job_id)
    if intent.get("state") != "submitted" or not intent.get("providerRequestId"):
        raise FinalizationBlocked("Provider submission is unresolved")
    reservation = load(finance_table, "reservation:" + job_id)
    if reservation.get("projectId") != project_id or reservation.get("state") != "settled":
        raise FinalizationBlocked("Verified financial settlement required")
    etag = getattr(job, "metadata", {}).get("etag")
    if not etag:
        raise FinalizationBlocked("Job ETag required")
    updated = {
        **dict(job),
        "status": "completed",
        "billingStatus": "settled",
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        jobs_table.update_entity(
            entity=updated, mode=UpdateMode.REPLACE, etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        raise FinalizationBlocked("Concurrent completion; reread before retry") from exc
    return {"job_id": job_id, "status": "completed", "replayed": False}


def classify_paid_job_recovery(job, submission_intent=None, reservation=None):
    """Read-only crash diagnosis; never automatically submit or settle."""
    if job.get("status") == "completed":
        return "completed"
    if job.get("status") != "processing":
        return "not_claimed"
    if job.get("blobName") and reservation and reservation.get("state") == "settled":
        return "ready_to_finalize"
    if submission_intent is None:
        return "submission_intent_unverified"
    if submission_intent.get("state") == "submitting":
        return "manual_review_ambiguous_provider_submission"
    if submission_intent.get("state") == "submitted":
        if job.get("blobName"):
            return "awaiting_verified_billing"
        if submission_intent.get("statusUrl") and submission_intent.get("responseUrl"):
            return "resume_existing_poll_only"
    return "manual_review_missing_evidence"

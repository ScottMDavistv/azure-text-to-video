"""Explicitly gated queue-to-budgeted-FAL worker integration.

NOT selected by worker_cli, worker_service, or deployed Container App.
Never starts a background poll loop. Requires trusted dependencies.
"""

import os

from consumer import consume_one
from jobs import claim_job, transition_job
from finance_controls import check_id
from fal_budget_guard import verify_job_reservation
from budgeted_fal_execution import run_reserved_fal_job
from storage import upload_rendered_video
from video_download import download_mp4
from video_provider import validate_mp4


class PaidWorkerDisabled(RuntimeError):
    pass


def paid_worker_enabled():
    """Both deployment operator and call site must opt in."""
    return os.getenv("ENABLE_PAID_FAL_WORKER", "").strip().lower() == "true"


def process_reserved_job(job_id, *, enabled=False, jobs_table,
                         finance_table, submission_table, client,
                         authorize_job, download=download_mp4,
                         upload=upload_rendered_video):
    """Claim and run exactly one reserved job, with no automatic POST retry.

    Queued job must already carry server-validated projectId.
    A successful provider result is NOT a verified charge; job is
    deliberately left processing for a separate billing/recovery
    procedure rather than prematurely marked completed.
    """
    if enabled is not True or not paid_worker_enabled():
        raise PaidWorkerDisabled("Paid FAL worker is disabled")
    check_id(job_id)
    original = jobs_table.get_entity(partition_key="video", row_key=job_id)
    project_id = check_id(original.get("projectId"))
    if original.get("status") != "queued":
        return {"job_id": job_id, "status": "skipped"}
    if not callable(authorize_job) or authorize_job(job_id, project_id) is not True:
        raise PaidWorkerDisabled("Trusted job authorization required")
    if verify_job_reservation(finance_table, job_id, project_id) is not True:
        raise PaidWorkerDisabled("Durable budget reservation required")

    claimed = claim_job(job_id)  # conditional ETag claim
    if claimed is None:
        return {"job_id": job_id, "status": "skipped"}

    # A provider timeout or crash intentionally leaves processing state
    # for manual reconciliation. Never call the provider a second time.
    result = run_reserved_fal_job(
        enabled=True, job_id=job_id, jobs_table=jobs_table,
        finance_table=finance_table,
        submission_table=submission_table, client=client,
        authorize_job=authorize_job,
    )
    video = validate_mp4(download(result["video_url"]))
    blob = upload(job_id, video)
    return {
        "job_id": job_id, "status": "awaiting_billing",
        "blob_name": blob, "billing_status": "awaiting_verified_charge",
    }


def run_budgeted_once(*, enabled=False, queue_client=None,
                      jobs_table, finance_table, submission_table,
                      client, authorize_job):
    """Consume one message only under explicit paid-worker kill switch.

    Not invoked by any existing worker entrypoint. Queue ack after
    processing means an external reconciliation scan must finalize.
    """
    if enabled is not True or not paid_worker_enabled():
        raise PaidWorkerDisabled("Paid FAL worker is disabled")
    return consume_one(
        queue_client=queue_client,
        processor=lambda job_id: process_reserved_job(
            job_id, enabled=True, jobs_table=jobs_table,
            finance_table=finance_table, submission_table=submission_table,
            client=client, authorize_job=authorize_job,
        ),
    )

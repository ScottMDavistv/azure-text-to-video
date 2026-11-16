"""Explicitly gated queue-to-budgeted-FAL worker integration.

NOT selected by worker_cli, worker_service, or deployed Container App.
Never starts a background poll loop. Requires trusted dependencies.
"""

import os

from consumer import consume_one
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode
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


def _claim_on_table(table, job_id, original):
    etag = getattr(original, 'metadata', {}).get('etag')
    if not etag:
        raise PaidWorkerDisabled('Missing job concurrency ETag')
    updated = {**dict(original), 'status': 'processing'}
    try:
        table.update_entity(
            entity=updated, mode=UpdateMode.REPLACE,
            etag=etag, match_condition=MatchConditions.IfNotModified,
        )
    except HttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise
    return True


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

    claimed = _claim_on_table(jobs_table, job_id, original)
    if not claimed:
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
    # Keep financial settlement separate, but persist the finished
    # artifact so crash recovery can locate it without resubmitting.
    latest = jobs_table.get_entity(partition_key='video', row_key=job_id)
    if latest.get('status') != 'processing':
        raise PaidWorkerDisabled('Claimed job changed before artifact persistence')
    tag = getattr(latest, 'metadata', {}).get('etag')
    if not tag:
        raise PaidWorkerDisabled('Missing artifact persistence ETag')
    jobs_table.update_entity(
        entity={**dict(latest), 'blobName': blob,
                'billingStatus': 'awaiting_verified_charge'},
        mode=UpdateMode.REPLACE, etag=tag,
        match_condition=MatchConditions.IfNotModified,
    )
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

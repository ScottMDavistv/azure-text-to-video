from datetime import datetime, timezone

from jobs import claim_job, transition_job
from storage import upload_rendered_video


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def process_job(job_id, renderer):
    """
    Process one queued video job.

    renderer(prompt) must return nonempty MP4 bytes.

    Returns skipped when another worker has already claimed
    the job or it is no longer queued.
    """
    if renderer is None:
        raise ValueError("A renderer is required")

    entity = claim_job(job_id)

    if entity is None:
        return {
            "job_id": str(job_id),
            "status": "skipped",
            "reason": "Job is not available for claiming",
        }

    prompt = entity["prompt"]

    try:
        video_bytes = renderer(prompt)

        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise ValueError("Renderer returned no MP4 data")

        blob_name = upload_rendered_video(
            job_id,
            video_bytes,
        )

        transition_job(
            job_id,
            "completed",
            blobName=blob_name,
            completedAt=utc_now(),
        )

        return {
            "job_id": str(job_id),
            "status": "completed",
            "blob_name": blob_name,
        }

    except Exception as exc:
        transition_job(
            job_id,
            "failed",
            errorType=type(exc).__name__,
            failedAt=utc_now(),
        )
        raise

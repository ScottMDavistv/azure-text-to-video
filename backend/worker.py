from datetime import datetime, timezone

from jobs import transition_job
from storage import get_jobs_table_client, upload_rendered_video


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def process_job(job_id, renderer):
    """
    Process one queued job.

    renderer(prompt) must return nonempty MP4 bytes.

    This function is not started automatically.
    A real video-generation provider must be connected.
    """
    table = get_jobs_table_client()

    entity = table.get_entity(
        partition_key="video",
        row_key=str(job_id),
    )

    if entity["status"] != "queued":
        raise ValueError("Job must be queued before processing")

    prompt = entity["prompt"]

    transition_job(
        job_id,
        "processing",
        startedAt=utc_now(),
    )

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

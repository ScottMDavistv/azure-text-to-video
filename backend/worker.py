from datetime import datetime, timezone

from jobs import claim_job, transition_job
from storage import upload_rendered_video


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def process_job(
    job_id,
    renderer,
    *,
    render_input_builder=None,
    artifact_writer=None,
):
    """
    Process one queued video job.

    renderer(render_input) must return nonempty MP4 bytes. By default,
    render_input is the stored prompt.

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

    try:
        render_input = (
            entity["prompt"]
            if render_input_builder is None
            else render_input_builder(entity)
        )
        video_bytes = renderer(render_input)

        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise ValueError("Renderer returned no MP4 data")

        writer = (
            upload_rendered_video
            if artifact_writer is None
            else artifact_writer
        )
        blob_name = writer(
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


def process_synthetic_job(job_id, renderer, *, artifact_writer):
    """Run the offline scene-plan seam using explicitly injected side effects."""
    if not callable(renderer):
        raise ValueError("A synthetic renderer is required")
    if not callable(artifact_writer):
        raise ValueError("An artifact writer is required")

    import os

    for flag in (
        "ENABLE_PAID_FAL_WORKER",
        "ENABLE_LIVE_VIDEO_RENDERING",
        "ENABLE_WORKER_CONSUMPTION",
    ):
        if os.getenv(flag, "").strip().lower() in {"1", "true", "yes"}:
            raise ValueError(f"Synthetic rendering is disabled when {flag} is enabled")

    from fal_renderer import FalVideoRenderer

    if isinstance(renderer, FalVideoRenderer):
        raise ValueError("A paid provider renderer cannot be used for synthetic jobs")

    from synthetic_scene_plan import build_scene_plan_from_job

    def write_expected_artifact(render_job_id, content):
        expected_name = f"renders/{render_job_id}.mp4"
        actual_name = artifact_writer(render_job_id, content)
        if actual_name != expected_name:
            raise ValueError("Artifact writer returned an unexpected name")
        return actual_name

    return process_job(
        job_id,
        renderer,
        render_input_builder=build_scene_plan_from_job,
        artifact_writer=write_expected_artifact,
    )

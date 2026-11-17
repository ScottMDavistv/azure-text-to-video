"""Explicit manual, bounded synthetic queue worker; never paid rendering.

Not enabled by the existing worker Container Apps Job or default Docker CMD.
Runtime must be independently reviewed before any Azure scheduling/activation.
"""
import os
import sys
from uuid import UUID

from consumer import consume_one
from job_queue import get_synthetic_queue_client, synthetic_queue_name
from project_models import validate_id
from storage import get_jobs_table_client, upload_rendered_video
from synthetic_job_renderer import assert_offline, render_scene_plan, SyntheticRenderError
from worker import process_synthetic_job

def require_synthetic_readiness():
    assert_offline()
    try:
        synthetic_queue_name()
    except ValueError:
        raise SyntheticRenderError('Dedicated synthetic queue configuration required') from None
    for flag in ("ENABLE_AUTHENTICATED_VIDEO_JOBS", "ENABLE_PROJECT_ROUTES",
                 "ENABLE_SYNTHETIC_VIDEO_DISPATCH"):
        if os.getenv(flag, "").strip().lower() != "true":
            raise SyntheticRenderError("Explicit project synthetic worker authorization required")
    if os.getenv("ALLOW_UNAUTHENTICATED_DEV", "false").strip().lower() in {"1", "true", "yes"}:
        raise SyntheticRenderError("Unauthenticated legacy mode prohibited")

def process_owned_synthetic_job(job_id, *, renderer=render_scene_plan,
                                artifact_writer=upload_rendered_video):
    require_synthetic_readiness()
    canonical = str(UUID(str(job_id)))
    job = get_jobs_table_client().get_entity(partition_key="video", row_key=canonical)
    try:
        validate_id(job.get("projectId"))
        validate_id(job.get("createdBy"))
    except (ValueError, TypeError):
        raise SyntheticRenderError("Refusing legacy or non-project-owned job") from None
    if job.get("dispatchQueueType") != "synthetic":
        raise SyntheticRenderError("Synthetic-only queue job classification required")
    if job.get("status") != "queued":
        return {"job_id": canonical, "status": "skipped"}
    return process_synthetic_job(canonical, renderer, artifact_writer=artifact_writer)

def run_once(*, queue_client=None, renderer=render_scene_plan,
             artifact_writer=upload_rendered_video):
    require_synthetic_readiness()
    return consume_one(
        processor=lambda job_id: process_owned_synthetic_job(
            job_id, renderer=renderer, artifact_writer=artifact_writer),
        queue_client=queue_client if queue_client is not None else get_synthetic_queue_client(),
    )

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv != ["--once"]:
        print("Only explicit --once synthetic execution is supported", file=sys.stderr)
        return 2
    try:
        result = run_once()
    except Exception:
        # No access token, internal identifiers or raw FFmpeg/Azure error output.
        print("Synthetic worker execution blocked or failed", file=sys.stderr)
        return 2
    print(result.get("status", "unknown"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

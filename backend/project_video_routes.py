"""Development-only Entra-authenticated, project-owned video journey.

Legacy /jobs service-token endpoints are deliberately NOT exposed to browsers.
These routes use the existing signed Entra token verifier and server-recorded
project membership, and remain disabled until explicit rollout approval.
"""
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceNotFoundError
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from project_access import Action, AccessDenied
from project_oidc import ProjectIdentityError, ProjectScopeDenied, verify_project_token
from project_security import require_project_role
from storage import get_jobs_table_client
from video_routes import downloads_enabled, get_video_resource, MAX_DELIVERY_BYTES

router = APIRouter(prefix="/projects", tags=["project-video"])

class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)

def journey_enabled():
    return (os.getenv("ENABLE_AUTHENTICATED_VIDEO_JOBS", "").lower() == "true"
            and os.getenv("ENABLE_PROJECT_ROUTES", "").lower() == "true")

def verified_actor(table, authorization):
    if not journey_enabled():
        raise HTTPException(503, "Authenticated video jobs are disabled")
    if not isinstance(authorization, str):
        raise HTTPException(401, "Bearer token required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise HTTPException(401, "Bearer token required")
    try:
        return verify_project_token(token.strip(), table)
    except ProjectScopeDenied as exc:
        raise HTTPException(403, "Required API permission missing") from exc
    except ProjectIdentityError as exc:
        raise HTTPException(401, "Invalid user identity") from exc

def authorized_job(table, project_id, job_id, actor, action=Action.READ):
    try:
        require_project_role(table, actor, project_id, action)
        job = table.get_entity(partition_key="video", row_key=job_id)
    except ResourceNotFoundError:
        raise HTTPException(404, "Video job not found") from None
    except (AccessDenied, ValueError):
        raise HTTPException(403, "Project access denied") from None
    # Missing ownership fields on legacy jobs are NOT treated as public.
    if (job.get("projectId") != project_id or
            job.get("createdBy") != actor.user_id):
        raise HTTPException(404, "Video job not found")
    return job

def get_context(project_id, authorization, action):
    table = get_jobs_table_client()
    actor = verified_actor(table, authorization)
    try:
        require_project_role(table, actor, project_id, action)
    except (AccessDenied, ValueError):
        raise HTTPException(403, "Project access denied") from None
    return table, actor

def response_job(job):
    return {
        "job_id": job["RowKey"],
        "status": job["status"],
        "prompt": job["prompt"],
        "created_at": job["createdAt"],
    }

@router.post("/{project_id}/jobs", status_code=201)
def create_project_job(project_id: UUID, request: PromptRequest,
                       authorization: str | None = Header(default=None)):
    pid = str(project_id)
    try:
        table, actor = get_context(pid, authorization, Action.CREATE_JOB)
        now = datetime.now(timezone.utc).isoformat()
        job = {
            "PartitionKey": "video", "RowKey": str(uuid4()),
            "prompt": request.prompt.strip(), "status": "created",
            "createdAt": now, "projectId": pid, "createdBy": actor.user_id,
        }
        if not job["prompt"]:
            raise HTTPException(422, "Prompt cannot be blank")
        table.create_entity(entity=job)
        return response_job(job)
    except AzureError:
        raise HTTPException(503, "Project job storage unavailable") from None

@router.get("/{project_id}/jobs/{job_id}")
def read_project_job(project_id: UUID, job_id: UUID,
                     authorization: str | None = Header(default=None)):
    try:
        table = get_jobs_table_client()
        actor = verified_actor(table, authorization)
        job = authorized_job(table, str(project_id), str(job_id), actor)
        return response_job(job)
    except AzureError:
        raise HTTPException(503, "Project job storage unavailable") from None

def synthetic_dispatch_enabled():
    # A queued job must never enter a paid/live worker configuration, regardless
    # of which common affirmative environment spelling was used.
    unsafe = {"1", "true", "yes"}
    if os.getenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "").strip().lower() != "true":
        return False
    if os.getenv("VIDEO_RENDER_PROVIDER", "disabled").strip().lower() != "disabled":
        return False
    if os.getenv("VIDEO_PROVIDER", "disabled").strip().lower() != "disabled":
        return False
    return all(
        os.getenv(name, "false").strip().lower() not in unsafe
        for name in (
            "ENABLE_PAID_FAL_WORKER", "ENABLE_LIVE_VIDEO_RENDERING",
            "ENABLE_WORKER_CONSUMPTION", "ALLOW_UNAUTHENTICATED_DEV",
        )
    )

@router.post("/{project_id}/jobs/{job_id}/queue")
def queue_project_job(project_id: UUID, job_id: UUID,
                      authorization: str | None = Header(default=None)):
    from jobs import transition_job
    from dispatch_recovery import (DispatchConflict, DispatchUncertain,
                                   dispatch_queued_job)
    try:
        table = get_jobs_table_client()
        actor = verified_actor(table, authorization)
        authorized_job(table, str(project_id), str(job_id), actor, Action.CREATE_JOB)
        if not synthetic_dispatch_enabled():
            raise HTTPException(503, "Synthetic dispatch is disabled")
        transition_job(str(job_id), "queued", dispatchState="pending")
        result = dispatch_queued_job(str(job_id))
        return {"job_id": str(job_id), "status": "queued",
                "dispatched": result["dispatched"]}
    except (DispatchConflict, ValueError) as exc:
        raise HTTPException(409, "Job dispatch conflict") from exc
    except DispatchUncertain as exc:
        raise HTTPException(503, "Dispatch outcome uncertain; recovery required") from exc
    except AzureError:
        raise HTTPException(503, "Job storage unavailable") from None

def authorized_video(project_id, job_id, authorization):
    table = get_jobs_table_client()
    actor = verified_actor(table, authorization)
    if not downloads_enabled():
        raise HTTPException(503, "Video downloads are disabled")
    job = authorized_job(table, str(project_id), str(job_id), actor)
    if job.get("status") != "completed":
        raise HTTPException(409, "Video generation is not completed")
    return get_video_resource(str(job_id))

@router.get("/{project_id}/jobs/{job_id}/video/metadata")
def project_video_metadata(project_id: UUID, job_id: UUID,
                           authorization: str | None = Header(default=None)):
    try:
        video = authorized_video(project_id, job_id, authorization)
        return {"job_id": str(job_id), "status": "completed",
                "available": True, "content_type": "video/mp4",
                "size_bytes": video["size_bytes"],
                "download_endpoint": f"/projects/{project_id}/jobs/{job_id}/video"}
    except AzureError:
        raise HTTPException(503, "Video storage unavailable") from None

@router.get("/{project_id}/jobs/{job_id}/video")
def project_video_download(project_id: UUID, job_id: UUID,
                           authorization: str | None = Header(default=None)):
    try:
        video = authorized_video(project_id, job_id, authorization)
    except AzureError:
        raise HTTPException(503, "Video storage unavailable") from None
    def chunks():
        total = 0
        for chunk in video["blob"].download_blob(
                max_concurrency=1, etag=video["etag"],
                match_condition=MatchConditions.IfNotModified).chunks():
            total += len(chunk)
            if total > MAX_DELIVERY_BYTES or total > video["size_bytes"]:
                raise RuntimeError("Video exceeds verified content length")
            yield chunk
        if total != video["size_bytes"]:
            raise RuntimeError("Video shorter than verified content length")
    return StreamingResponse(
        chunks(), media_type="video/mp4", headers={
            "Content-Length": str(video["size_bytes"]),
            "Content-Disposition": f'inline; filename="{job_id}.mp4"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Accept-Ranges": "none",
        })

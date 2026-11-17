"""Opt-in per-user video jobs. Separate from private legacy /jobs service routes.

No public exposure or runtime activation is authorized by this implementation.
Project identity and membership are resolved from signed Entra tokens and server data.
"""
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError, ResourceNotFoundError
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from project_access import Action, AccessDenied
from project_oidc import ProjectIdentityError, ProjectScopeDenied, verify_project_token
from project_security import require_project_role
from storage import get_jobs_table_client
from video_routes import downloads_enabled, get_video_resource

router = APIRouter(prefix="/user/jobs", tags=["user-video"])
JOB_OWNER_FIELD = "ownerUserId"
JOB_PROJECT_FIELD = "projectId"


def enabled():
    return (
        os.getenv("ENABLE_USER_VIDEO_JOBS", "").strip().lower() == "true"
        and os.getenv("ENABLE_PROJECT_ROUTES", "").strip().lower() == "true"
    )


def require_enabled():
    if not enabled():
        raise HTTPException(503, "User video jobs are disabled")


def actor_from_bearer(table, authorization):
    require_enabled()
    if not isinstance(authorization, str):
        raise HTTPException(401, "Bearer access token required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Bearer access token required")
    try:
        return verify_project_token(token.strip(), table)
    except ProjectScopeDenied as exc:
        raise HTTPException(403, "Delegated API permission required") from exc
    except ProjectIdentityError as exc:
        raise HTTPException(401, "Invalid user identity") from exc


def membership(table, actor, project_id, action):
    try:
        require_project_role(table, actor, project_id, action)
    except (AccessDenied, ValueError, KeyError) as exc:
        raise HTTPException(403, "Project access denied") from exc


def stored_job(table, job_id, actor, action=Action.READ):
    try:
        entity = table.get_entity(partition_key="video", row_key=str(job_id))
    except ResourceNotFoundError:
        raise HTTPException(404, "Video job not found") from None

    # Unowned legacy jobs, malformed rows and wrong owners all fail closed.
    if entity.get("ownerUserId") != actor.user_id:
        raise HTTPException(404, "Video job not found")
    project_id = entity.get("projectId")
    try:
        project_id = str(UUID(project_id))
        if project_id != entity.get("projectId"):
            raise ValueError("Noncanonical project ID")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Video job not found") from None
    membership(table, actor, project_id, action)
    return entity


class UserJobRequest(BaseModel):
    project_id: UUID
    prompt: str = Field(min_length=1, max_length=2000)


@router.post("", status_code=201)
def create_user_job(
    request: UserJobRequest,
    authorization: str | None = Header(default=None),
):
    require_enabled()
    try:
        table = get_jobs_table_client()
        actor = actor_from_bearer(table, authorization)
        project_id = str(request.project_id)
        membership(table, actor, project_id, Action.CREATE_JOB)
        job_id = str(uuid4())
        created = datetime.now(timezone.utc).isoformat()
        # Never accept owner/user/status/role from request body.
        table.create_entity(entity={
            "PartitionKey": "video", "RowKey": job_id,
            "ownerUserId": actor.user_id, "projectId": project_id,
            "prompt": request.prompt, "status": "created", "createdAt": created,
        })
        return {"job_id": job_id, "status": "created", "created_at": created}
    except AzureError as exc:
        raise HTTPException(503, "Job storage unavailable") from exc


@router.get("/{job_id}")
def get_user_job(
    job_id: UUID,
    authorization: str | None = Header(default=None),
):
    require_enabled()
    try:
        table = get_jobs_table_client()
        actor = actor_from_bearer(table, authorization)
        entity = stored_job(table, job_id, actor)
        return {
            "job_id": entity["RowKey"], "prompt": entity["prompt"],
            "status": entity["status"], "created_at": entity["createdAt"],
        }
    except AzureError as exc:
        raise HTTPException(503, "Job storage unavailable") from exc


@router.post("/{job_id}/queue")
def queue_user_job(
    job_id: UUID,
    authorization: str | None = Header(default=None),
):
    require_enabled()
    from jobs import transition_job
    from dispatch_recovery import (
        DispatchConflict, DispatchUncertain, dispatch_queued_job,
    )
    try:
        table = get_jobs_table_client()
        actor = actor_from_bearer(table, authorization)
        stored_job(table, job_id, actor, Action.CREATE_JOB)
        # Revalidate on the same row just before transition; legacy service
        # dispatch controls, compare-and-swap and retries remain authoritative.
        transition_job(str(job_id), "queued", dispatchState="pending")
        try:
            result = dispatch_queued_job(str(job_id))
        except DispatchUncertain as exc:
            raise HTTPException(503, "Job queued; dispatch recovery required") from exc
        except DispatchConflict as exc:
            raise HTTPException(409, "Dispatch conflict") from exc
        return {"job_id": str(job_id), "status": "queued", "dispatched": result["dispatched"]}
    except ResourceNotFoundError:
        raise HTTPException(404, "Video job not found") from None
    except ValueError as exc:
        raise HTTPException(409, "Job transition rejected") from exc
    except AzureError as exc:
        raise HTTPException(503, "Job storage unavailable") from exc


def authorized_video(job_id, authorization):
    require_enabled()
    if not downloads_enabled():
        raise HTTPException(503, "Video downloads are disabled")
    try:
        table = get_jobs_table_client()
        actor = actor_from_bearer(table, authorization)
        entity = stored_job(table, job_id, actor)
        if entity.get("status") != "completed":
            raise HTTPException(409, "Video generation is not completed")
        # Blob access is private and shared with existing bounded streaming logic.
        return get_video_resource(str(job_id))
    except AzureError as exc:
        raise HTTPException(503, "Job or video storage unavailable") from exc


@router.get("/{job_id}/video/metadata")
def user_video_metadata(
    job_id: UUID,
    authorization: str | None = Header(default=None),
):
    resource = authorized_video(job_id, authorization)
    return {
        "job_id": resource["job_id"], "status": "completed",
        "available": True, "content_type": "video/mp4",
        "size_bytes": resource["size_bytes"],
        "download_endpoint": f"/user/jobs/{resource['job_id']}/video",
    }


@router.get("/{job_id}/video")
def user_video_stream(
    job_id: UUID,
    authorization: str | None = Header(default=None),
):
    from fastapi.responses import StreamingResponse
    from video_routes import MAX_DELIVERY_BYTES
    resource = authorized_video(job_id, authorization)

    def chunks():
        count = 0
        for chunk in resource["blob"].download_blob(max_concurrency=1).chunks():
            count += len(chunk)
            if count > MAX_DELIVERY_BYTES:
                raise RuntimeError("Video exceeded delivery size limit")
            yield chunk

    return StreamingResponse(
        chunks(), media_type="video/mp4",
        headers={
            "Content-Length": str(resource["size_bytes"]),
            "Content-Disposition": f'inline; filename="{resource["job_id"]}.mp4"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Accept-Ranges": "none",
        },
    )

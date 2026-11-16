"""Opt-in OIDC-authenticated project held-jobs endpoints.

Fail closed with 503 until an OIDC verifier is configured.
No paid provider operations or budget writes are exposed.
"""

import os
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from azure.core.exceptions import AzureError

from project_access import Action, AccessDenied
from project_security import require_project_role
from project_oidc import ProjectIdentityError, ProjectScopeDenied, verify_project_token
from held_jobs import held_job_dashboard, cancel_held_job, HeldJobDenied
from storage import get_jobs_table_client

router = APIRouter(prefix="/projects", tags=["projects"])


def project_api_enabled():
    return os.getenv("ENABLE_PROJECT_ROUTES", "").strip().lower() == "true"


def verified_request_actor(table, authorization):
    if not project_api_enabled():
        raise HTTPException(503, "Project endpoints are disabled")
    if not isinstance(authorization, str):
        raise HTTPException(401, "Bearer token required")
    scheme, sep, token = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer token required")
    try:
        return verify_project_token(token.strip(), table)
    except ProjectScopeDenied as exc:
        raise HTTPException(403, "Required API permission missing") from exc
    except ProjectIdentityError as exc:
        raise HTTPException(401, "Invalid project identity") from exc


@router.get("/{project_id}/holds")
def project_holds_endpoint(project_id: UUID,
                           authorization: str | None = Header(default=None)):
    if not project_api_enabled():
        raise HTTPException(503, "Project endpoints are disabled")
    try:
        table = get_jobs_table_client()
        actor = verified_request_actor(table, authorization)
        require_project_role(table, actor, str(project_id), Action.READ)
        return {"project_id": str(project_id),
                "jobs": held_job_dashboard(table, str(project_id))}
    except (AccessDenied, ValueError) as exc:
        raise HTTPException(403, "Project access denied") from exc
    except AzureError as exc:
        raise HTTPException(503, "Project storage unavailable") from exc


@router.post("/{project_id}/holds/{job_id}/cancel")
def cancel_project_hold_endpoint(project_id: UUID, job_id: UUID,
                                 authorization: str | None = Header(default=None)):
    if not project_api_enabled():
        raise HTTPException(503, "Project endpoints are disabled")
    try:
        table = get_jobs_table_client()
        actor = verified_request_actor(table, authorization)
        require_project_role(table, actor, str(project_id), Action.CANCEL_JOB)
        outcome = cancel_held_job(
            table, str(job_id), actor_id=actor.user_id,
            authorize_cancellation=lambda actor_id, pid, jid:
                actor_id == actor.user_id and pid == str(project_id),
        )
        return {"job_id": str(job_id), "status": outcome}
    except (AccessDenied, HeldJobDenied, ValueError) as exc:
        raise HTTPException(403, "Project hold cancellation denied") from exc
    except AzureError as exc:
        raise HTTPException(503, "Project storage unavailable") from exc

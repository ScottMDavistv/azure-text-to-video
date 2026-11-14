"""
Protected administrative job-management API.

Not suitable for public user-facing access.

All routes enforce the admin feature flag and token.
"""

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Header, HTTPException, Query

import job_management
from job_admin_auth import authorize_job_admin


router = APIRouter(
    prefix="/admin/jobs",
    tags=["job-administration"],
)


def map_job_error(exc):
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    if isinstance(exc, job_management.JobNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Video job not found",
        ) from exc

    if isinstance(exc, job_management.JobConflictError):
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    if isinstance(exc, AzureError):
        raise HTTPException(
            status_code=503,
            detail="Job storage unavailable",
        ) from exc

    raise exc


@router.get("")
def list_jobs_endpoint(
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    authorization: str | None = Header(default=None),
):
    authorize_job_admin(authorization)

    try:
        return job_management.list_jobs(
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except (
        ValueError,
        AzureError,
    ) as exc:
        map_job_error(exc)


@router.get("/{job_id}")
def get_job_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
):
    authorize_job_admin(authorization)

    try:
        return job_management.get_job_details(job_id)
    except (
        ValueError,
        job_management.JobNotFoundError,
        AzureError,
    ) as exc:
        map_job_error(exc)


@router.post("/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
):
    authorize_job_admin(authorization)

    try:
        result = job_management.cancel_job(job_id)

        from operational_events import log_event
        log_event(
            "job_cancelled",
            job_id=result["job_id"],
            status="cancelled",
        )

        return result
    except (
        ValueError,
        job_management.JobNotFoundError,
        job_management.JobConflictError,
        AzureError,
    ) as exc:
        map_job_error(exc)

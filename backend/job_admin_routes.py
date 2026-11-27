"""
Protected administrative job-management API.

Not suitable for public user-facing access.

All routes enforce the admin feature flag and token.
"""

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Header, HTTPException, Query

import job_management
import stale_jobs
from job_admin_auth import authorize_job_admin


router = APIRouter(
    prefix="/admin/jobs",
    tags=["job-administration"],
)


def map_job_error(exc):
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid job administration request",
        ) from exc

    if isinstance(exc, job_management.JobNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="Video job not found",
        ) from exc

    if isinstance(exc, job_management.JobConflictError):
        raise HTTPException(
            status_code=409,
            detail="Job state changed or operation is not permitted",
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


@router.get("/stale")
def list_stale_jobs_endpoint(
    stale_minutes: int = Query(default=60, ge=1, le=10080),
    limit: int = Query(default=50, ge=1, le=50),
    authorization: str | None = Header(default=None),
):
    """Read-only inspection; a timestamp does not authorize job recovery."""
    authorize_job_admin(authorization)
    if type(stale_minutes) is not int or not 1 <= stale_minutes <= 10080:
        raise HTTPException(status_code=400, detail="Invalid stale-job threshold")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise HTTPException(status_code=400, detail="Invalid result limit")

    try:
        jobs = stale_jobs.find_stale_jobs(
            stale_minutes=stale_minutes, limit=limit
        )
    except (ValueError, AzureError) as exc:
        map_job_error(exc)

    return {
        "jobs": jobs,
        "count": len(jobs),
        "stale_minutes": stale_minutes,
        "limit": limit,
        "recovery": "manual_confirmation_required",
    }


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

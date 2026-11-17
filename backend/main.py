from datetime import datetime, timezone
from uuid import UUID, uuid4

from azure.core.exceptions import ResourceNotFoundError
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from storage import get_jobs_table_client


app = FastAPI(
    title="Azure Text-to-Video API",
    description="Backend API for cinematic AI video generation",
    version="0.2.0",
)

from video_routes import router as video_router
app.include_router(video_router)

from job_admin_routes import router as job_admin_router
app.include_router(job_admin_router)

from project_api_routes import router as project_router
app.include_router(project_router)

from project_video_routes import router as project_video_router
app.include_router(project_video_router)

from api_access import JobApiAuthMiddleware
app.add_middleware(JobApiAuthMiddleware)


class VideoJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@app.get("/")
def root():
    return {
        "application": "Azure Text-to-Video",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "textvideo-api",
    }


@app.post("/jobs", status_code=201)
def create_job(request: VideoJobRequest):
    job_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    entity = {
        "PartitionKey": "video",
        "RowKey": job_id,
        "prompt": request.prompt,
        "status": "created",
        "createdAt": created_at,
    }

    get_jobs_table_client().create_entity(entity=entity)

    return {
        "job_id": job_id,
        "status": "created",
        "created_at": created_at,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: UUID):
    try:
        entity = get_jobs_table_client().get_entity(
            partition_key="video",
            row_key=str(job_id),
        )
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Video job not found",
        )

    return {
        "job_id": entity["RowKey"],
        "prompt": entity["prompt"],
        "status": entity["status"],
        "created_at": entity["createdAt"],
    }


@app.post("/jobs/{job_id}/queue")
def queue_job(job_id: UUID):
    from jobs import transition_job
    from dispatch_recovery import (
        DispatchConflict, DispatchUncertain, dispatch_queued_job,
    )

    try:
        # The queued status and pending-dispatch marker commit together.
        transition_job(str(job_id), "queued", dispatchState="pending")
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Video job not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        result = dispatch_queued_job(str(job_id))
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Video job not found")
    except DispatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DispatchUncertain as exc:
        raise HTTPException(
            status_code=503,
            detail="Job queued, but dispatch outcome is uncertain; recovery required",
        ) from exc

    return {
        "job_id": str(job_id),
        "status": "queued",
        "dispatched": result["dispatched"],
    }


@app.post("/jobs/{job_id}/retry-dispatch")
def retry_job_dispatch(job_id: UUID):
    """Safe, lease-based redispatch; duplicate queue messages may still occur."""
    from dispatch_recovery import (
        DispatchConflict, DispatchUncertain, dispatch_queued_job,
    )

    try:
        result = dispatch_queued_job(str(job_id))
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Video job not found")
    except DispatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DispatchUncertain as exc:
        raise HTTPException(
            status_code=503,
            detail="Queue delivery is uncertain; retry only after lease expiry",
        ) from exc

    return {
        "job_id": str(job_id),
        "status": "queued",
        "dispatched": result["dispatched"],
        "recovery": True,
        "dispatch_outcome": result["status"],
    }

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

    try:
        result = transition_job(str(job_id), "queued")
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Video job not found",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return {
        "job_id": str(job_id),
        "status": result["status"],
    }

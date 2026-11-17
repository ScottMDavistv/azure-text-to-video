"""
Private completed-video delivery endpoints.

GET /jobs/{job_id}/video/metadata
GET /jobs/{job_id}/video

Both endpoints are disabled by default.

Temporary service-level Bearer-token authentication is required
when enabled. User-level authorization must be implemented
before any public exposure.
"""

import hmac
import os
from uuid import UUID

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

import storage


router = APIRouter()

MAX_DELIVERY_BYTES = 100 * 1024 * 1024


def downloads_enabled():
    return os.getenv(
        "ENABLE_VIDEO_DOWNLOADS", ""
    ).strip().lower() in {"1", "true", "yes"}


def authorize_video_download(authorization=None):
    if not downloads_enabled():
        raise HTTPException(
            status_code=503,
            detail="Video downloads are disabled",
        )

    expected_token = os.getenv(
        "VIDEO_DOWNLOAD_TOKEN", ""
    ).strip()

    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Video download authentication is not configured",
        )

    supplied_token = ""

    if isinstance(authorization, str):
        scheme, separator, token = authorization.partition(" ")

        if separator and scheme.lower() == "bearer":
            supplied_token = token.strip()

    if not hmac.compare_digest(
        supplied_token,
        expected_token,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid video access token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def normalize_job_id(job_id):
    try:
        return str(UUID(str(job_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid job ID",
        ) from None


def get_video_resource(job_id):
    """
    Resolve a completed job to its private blob.

    Blob names are derived from validated job IDs rather
    than accepting arbitrary caller-supplied paths.
    """
    job_id = normalize_job_id(job_id)

    try:
        table = storage.get_jobs_table_client()

        job = table.get_entity(
            partition_key="video",
            row_key=job_id,
        )

    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Video job not found",
        ) from None

    except AzureError:
        raise HTTPException(
            status_code=503,
            detail="Job storage unavailable",
        ) from None

    if job.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="Video generation is not completed",
        )

    account = os.getenv(
        "AZURE_STORAGE_ACCOUNT",
        "sttextvideo1794671698",
    )

    container = os.getenv(
        "AZURE_BLOB_CONTAINER",
        "videos",
    )

    blob_name = f"renders/{job_id}.mp4"

    try:
        service = BlobServiceClient(
            account_url=(
                f"https://{account}.blob.core.windows.net"
            ),
            credential=storage.get_credential(),
        )

        blob = service.get_blob_client(
            container=container,
            blob=blob_name,
        )

        properties = blob.get_blob_properties()

    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Completed video file not found",
        ) from None

    except AzureError:
        raise HTTPException(
            status_code=503,
            detail="Video storage unavailable",
        ) from None

    size = properties.size

    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise HTTPException(
            status_code=502,
            detail="Invalid stored video size",
        )

    if size > MAX_DELIVERY_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Video exceeds delivery size limit",
        )

    return {
        "job_id": job_id,
        "blob_name": blob_name,
        "size_bytes": size,
        "etag": properties.etag,
        "blob": blob,
    }


def get_video_metadata(
    job_id,
    authorization=None,
):
    authorize_video_download(authorization)
    resource = get_video_resource(job_id)

    return {
        "job_id": resource["job_id"],
        "status": "completed",
        "available": True,
        "content_type": "video/mp4",
        "size_bytes": resource["size_bytes"],
        "download_endpoint": (
            f"/jobs/{resource['job_id']}/video"
        ),
    }


def stream_video(
    job_id,
    authorization=None,
):
    authorize_video_download(authorization)
    resource = get_video_resource(job_id)

    def video_chunks():
        downloaded = 0
        downloader = resource["blob"].download_blob(
            max_concurrency=1,
        )

        for chunk in downloader.chunks():
            downloaded += len(chunk)

            if downloaded > MAX_DELIVERY_BYTES:
                raise RuntimeError(
                    "Video exceeded configured delivery limit"
                )

            yield chunk

    headers = {
        "Content-Length": str(resource["size_bytes"]),
        "Content-Disposition": (
            f'inline; filename="{resource["job_id"]}.mp4"'
        ),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "none",
    }

    return StreamingResponse(
        video_chunks(),
        media_type="video/mp4",
        headers=headers,
    )


@router.get(
    "/jobs/{job_id}/video/metadata",
    tags=["video"],
)
def video_metadata_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
):
    return get_video_metadata(
        job_id,
        authorization=authorization,
    )


@router.get(
    "/jobs/{job_id}/video",
    tags=["video"],
)
def video_stream_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
):
    return stream_video(
        job_id,
        authorization=authorization,
    )

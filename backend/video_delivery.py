"""
Store completed Fal video output in private Azure Blob Storage.
"""

from storage import upload_rendered_video
from video_download import download_mp4


def store_generated_video(
    job_id,
    video_url,
    *,
    downloader=download_mp4,
    uploader=upload_rendered_video,
):
    """
    Download a generated MP4 and store it under the job ID.

    No public Blob URL or storage credentials are returned.
    """
    video_bytes = downloader(video_url)

    blob_name = uploader(
        job_id,
        video_bytes,
    )

    return {
        "job_id": str(job_id),
        "blob_name": blob_name,
        "size_bytes": len(video_bytes),
        "stored": True,
    }

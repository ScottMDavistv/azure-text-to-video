"""
Download completed Fal MP4 files with security and size limits.

No network requests occur until download_mp4() is called.
"""

from urllib.parse import urlparse

import requests

from provider_errors import RenderProviderError
from video_provider import validate_mp4


MAX_VIDEO_BYTES = 100 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class VideoDownloadError(RenderProviderError):
    def __init__(self, message, retryable=False):
        super().__init__(
            message,
            retryable=retryable,
            error_code="video_download_error",
        )


def validate_video_url(url):
    if not isinstance(url, str):
        raise VideoDownloadError("Invalid video URL")

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise VideoDownloadError("Invalid video URL") from exc

    hostname = (parsed.hostname or "").lower()

    if (
        parsed.scheme != "https"
        or not hostname
        or (
            hostname != "fal.media"
            and not hostname.endswith(".fal.media")
        )
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise VideoDownloadError(
            "Video URL must use an approved Fal media host"
        )

    return url


def download_mp4(
    url,
    *,
    session=None,
    max_bytes=MAX_VIDEO_BYTES,
    timeout=(10, 60),
):
    """
    Download a bounded MP4 from an approved Fal media host.

    Redirects are disabled to prevent requests from being
    redirected to untrusted services.
    """
    validate_video_url(url)

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ValueError("max_bytes must be an integer")

    if max_bytes < 12:
        raise ValueError("max_bytes is too small")

    client = session if session is not None else requests.Session()

    response = None

    try:
        response = client.get(
            url,
            stream=True,
            timeout=timeout,
            allow_redirects=False,
        )

        if 300 <= response.status_code < 400:
            raise VideoDownloadError(
                "Video download redirects are not permitted"
            )

        if response.status_code != 200:
            raise VideoDownloadError(
                f"Video download returned HTTP {response.status_code}",
                retryable=response.status_code in (
                    408, 429, 500, 502, 503, 504
                ),
            )

        content_length = response.headers.get("Content-Length")

        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError) as exc:
                raise VideoDownloadError(
                    "Invalid Content-Length"
                ) from exc

            if declared_size < 0 or declared_size > max_bytes:
                raise VideoDownloadError(
                    "Video exceeds maximum download size"
                )

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].strip().lower()

        if content_type and content_type not in (
            "video/mp4",
            "application/octet-stream",
            "binary/octet-stream",
        ):
            raise VideoDownloadError(
                "Unexpected video content type"
            )

        chunks = []
        total = 0

        for chunk in response.iter_content(
            chunk_size=CHUNK_SIZE
        ):
            if not chunk:
                continue

            total += len(chunk)

            if total > max_bytes:
                raise VideoDownloadError(
                    "Video exceeds maximum download size"
                )

            chunks.append(chunk)

        video_bytes = b"".join(chunks)

        try:
            return validate_mp4(video_bytes)
        except Exception as exc:
            raise VideoDownloadError(
                "Downloaded content is not a valid MP4 candidate"
            ) from exc

    except requests.Timeout as exc:
        raise VideoDownloadError(
            "Video download timed out",
            retryable=True,
        ) from exc

    except requests.RequestException as exc:
        raise VideoDownloadError(
            "Video download network failure",
            retryable=True,
        ) from exc

    finally:
        if response is not None:
            response.close()

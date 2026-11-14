from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import video_routes


JOB_ID = str(uuid4())
TOKEN = "local-unit-test-token"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_VIDEO_DOWNLOADS",
        "true",
    )
    monkeypatch.setenv(
        "VIDEO_DOWNLOAD_TOKEN",
        TOKEN,
    )
    return f"Bearer {TOKEN}"


def make_storage(status="completed", size=128):
    table = MagicMock()
    table.get_entity.return_value = {
        "status": status,
    }

    service = MagicMock()
    blob = MagicMock()
    properties = MagicMock()
    properties.size = size

    blob.get_blob_properties.return_value = properties
    service.get_blob_client.return_value = blob

    return table, service, blob


def test_downloads_disabled_by_default(monkeypatch):
    monkeypatch.delenv(
        "ENABLE_VIDEO_DOWNLOADS",
        raising=False,
    )

    with pytest.raises(HTTPException) as error:
        video_routes.get_video_metadata(
            JOB_ID,
            authorization=f"Bearer {TOKEN}",
        )

    assert error.value.status_code == 503


def test_missing_token_configuration(monkeypatch):
    monkeypatch.setenv(
        "ENABLE_VIDEO_DOWNLOADS",
        "true",
    )
    monkeypatch.delenv(
        "VIDEO_DOWNLOAD_TOKEN",
        raising=False,
    )

    with pytest.raises(HTTPException) as error:
        video_routes.authorize_video_download(
            "Bearer test"
        )

    assert error.value.status_code == 503


def test_no_authorization_rejected(enabled):
    with pytest.raises(HTTPException) as error:
        video_routes.get_video_metadata(
            JOB_ID,
            authorization=None,
        )

    assert error.value.status_code == 401


def test_wrong_authorization_rejected(enabled):
    with pytest.raises(HTTPException) as error:
        video_routes.authorize_video_download(
            "Bearer incorrect"
        )

    assert error.value.status_code == 401


def test_correct_authorization(enabled):
    video_routes.authorize_video_download(enabled)


def test_invalid_job_id_rejected(enabled):
    with pytest.raises(HTTPException) as error:
        video_routes.get_video_metadata(
            "../../other-job",
            authorization=enabled,
        )

    assert error.value.status_code == 400


def test_metadata_success(enabled):
    table, service, blob = make_storage(size=2048)

    with (
        patch(
            "video_routes.storage.get_jobs_table_client",
            return_value=table,
        ),
        patch(
            "video_routes.storage.get_credential",
            return_value=MagicMock(),
        ),
        patch(
            "video_routes.BlobServiceClient",
            return_value=service,
        ),
    ):
        result = video_routes.get_video_metadata(
            JOB_ID,
            authorization=enabled,
        )

    assert result["available"] is True
    assert result["status"] == "completed"
    assert result["size_bytes"] == 2048
    assert result["content_type"] == "video/mp4"

    service.get_blob_client.assert_called_once_with(
        container="videos",
        blob=f"renders/{JOB_ID}.mp4",
    )


def test_unfinished_job_rejected(enabled):
    table, service, blob = make_storage(
        status="processing"
    )

    with patch(
        "video_routes.storage.get_jobs_table_client",
        return_value=table,
    ):
        with pytest.raises(HTTPException) as error:
            video_routes.get_video_metadata(
                JOB_ID,
                authorization=enabled,
            )

    assert error.value.status_code == 409


def test_oversized_video_rejected(enabled):
    table, service, blob = make_storage(
        size=video_routes.MAX_DELIVERY_BYTES + 1
    )

    with (
        patch(
            "video_routes.storage.get_jobs_table_client",
            return_value=table,
        ),
        patch(
            "video_routes.storage.get_credential",
            return_value=MagicMock(),
        ),
        patch(
            "video_routes.BlobServiceClient",
            return_value=service,
        ),
    ):
        with pytest.raises(HTTPException) as error:
            video_routes.get_video_metadata(
                JOB_ID,
                authorization=enabled,
            )

    assert error.value.status_code == 413


def test_streaming_response(enabled):
    table, service, blob = make_storage(
        size=8
    )

    downloader = MagicMock()
    downloader.chunks.return_value = [
        b"1234",
        b"5678",
    ]
    blob.download_blob.return_value = downloader

    with (
        patch(
            "video_routes.storage.get_jobs_table_client",
            return_value=table,
        ),
        patch(
            "video_routes.storage.get_credential",
            return_value=MagicMock(),
        ),
        patch(
            "video_routes.BlobServiceClient",
            return_value=service,
        ),
    ):
        response = video_routes.stream_video(
            JOB_ID,
            authorization=enabled,
        )

        import asyncio

        async def collect_stream():
            return [
                chunk
                async for chunk in response.body_iterator
            ]

        chunks = asyncio.run(collect_stream())

    assert b"".join(chunks) == b"12345678"
    assert response.media_type == "video/mp4"
    assert response.headers["cache-control"] == (
        "private, no-store"
    )


def test_authentication_precedes_storage_access(enabled):
    with patch(
        "video_routes.storage.get_jobs_table_client"
    ) as table:
        with pytest.raises(HTTPException):
            video_routes.get_video_metadata(
                JOB_ID,
                authorization="Bearer wrong",
            )

    table.assert_not_called()


def test_router_paths_registered():
    routes = {
        route.path
        for route in video_routes.router.routes
    }

    assert "/jobs/{job_id}/video" in routes
    assert "/jobs/{job_id}/video/metadata" in routes

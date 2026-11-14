from unittest.mock import MagicMock

import pytest

from video_delivery import store_generated_video


def test_generated_video_is_stored():
    downloader = MagicMock(
        return_value=b"test-video-data"
    )

    uploader = MagicMock(
        return_value="renders/job-123.mp4"
    )

    result = store_generated_video(
        "job-123",
        "https://fal.media/files/video.mp4",
        downloader=downloader,
        uploader=uploader,
    )

    assert result["stored"] is True
    assert result["size_bytes"] == 15

    assert result["blob_name"] == (
        "renders/job-123.mp4"
    )

    uploader.assert_called_once_with(
        "job-123",
        b"test-video-data",
    )


def test_download_failure_prevents_upload():
    downloader = MagicMock(
        side_effect=RuntimeError(
            "Download failed"
        )
    )

    uploader = MagicMock()

    with pytest.raises(RuntimeError):
        store_generated_video(
            "job-123",
            "https://fal.media/files/video.mp4",
            downloader=downloader,
            uploader=uploader,
        )

    uploader.assert_not_called()

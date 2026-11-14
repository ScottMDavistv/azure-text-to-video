from unittest.mock import MagicMock, patch

import pytest

from fal_renderer import FalVideoRenderer
from video_provider import VideoProviderError


TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def test_renderer_returns_mp4_bytes():
    client = MagicMock()
    downloader = MagicMock(return_value=TEST_MP4)

    with patch("fal_renderer.submit_and_wait") as submit:
        submit.return_value = {
            "video_url": "https://fal.media/files/video.mp4"
        }

        renderer = FalVideoRenderer(
            client,
            downloader=downloader,
        )

        result = renderer("Cinematic ocean sunrise")

    assert result == TEST_MP4

    submit.assert_called_once_with(
        client,
        "Cinematic ocean sunrise",
        timeout_seconds=900,
        poll_interval_seconds=10,
    )

    downloader.assert_called_once_with(
        "https://fal.media/files/video.mp4"
    )


def test_renderer_rejects_invalid_mp4():
    downloader = MagicMock(return_value=b"invalid")

    with patch("fal_renderer.submit_and_wait") as submit:
        submit.return_value = {
            "video_url": "https://fal.media/files/video.mp4"
        }

        renderer = FalVideoRenderer(
            MagicMock(),
            downloader=downloader,
        )

        with pytest.raises(VideoProviderError):
            renderer("Mountain sunset")


def test_renderer_rejects_empty_prompt():
    renderer = FalVideoRenderer(MagicMock())

    with pytest.raises(ValueError):
        renderer("   ")


def test_renderer_requires_client():
    with pytest.raises(ValueError):
        FalVideoRenderer(None)


def test_renderer_propagates_provider_error():
    client = MagicMock()

    with patch("fal_renderer.submit_and_wait") as submit:
        submit.side_effect = RuntimeError(
            "Provider unavailable"
        )

        renderer = FalVideoRenderer(client)

        with pytest.raises(RuntimeError):
            renderer("Cinematic forest")

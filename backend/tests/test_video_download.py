from unittest.mock import MagicMock

import pytest
import requests

from video_download import (
    VideoDownloadError,
    download_mp4,
    validate_video_url,
)


VIDEO_URL = "https://fal.media/files/example.mp4"

TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def make_session(
    content=TEST_MP4,
    status=200,
    content_type="video/mp4",
):
    session = MagicMock()
    response = MagicMock()

    response.status_code = status
    response.headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(content)),
    }

    response.iter_content.return_value = [content]
    session.get.return_value = response

    return session, response


def test_valid_fal_url():
    assert validate_video_url(VIDEO_URL) == VIDEO_URL


def test_fal_subdomain_allowed():
    assert validate_video_url(
        "https://v3.fal.media/files/video.mp4"
    )


def test_localhost_rejected():
    with pytest.raises(VideoDownloadError):
        validate_video_url(
            "http://127.0.0.1/internal"
        )


def test_lookalike_domain_rejected():
    with pytest.raises(VideoDownloadError):
        validate_video_url(
            "https://fal.media.attacker.example/video.mp4"
        )


def test_successful_download():
    session, response = make_session()

    result = download_mp4(
        VIDEO_URL,
        session=session,
    )

    assert result == TEST_MP4

    assert session.get.call_args.kwargs[
        "allow_redirects"
    ] is False

    response.close.assert_called_once()


def test_redirect_rejected():
    session, _ = make_session(status=302)

    with pytest.raises(VideoDownloadError):
        download_mp4(
            VIDEO_URL,
            session=session,
        )


def test_content_length_limit():
    session, response = make_session()

    response.headers["Content-Length"] = str(
        200 * 1024 * 1024
    )

    with pytest.raises(VideoDownloadError):
        download_mp4(
            VIDEO_URL,
            session=session,
        )

    response.iter_content.assert_not_called()


def test_streaming_size_limit():
    session, response = make_session()

    response.headers.pop("Content-Length")
    response.iter_content.return_value = [
        TEST_MP4,
        b"x" * 100,
    ]

    with pytest.raises(VideoDownloadError):
        download_mp4(
            VIDEO_URL,
            session=session,
            max_bytes=50,
        )


def test_html_rejected():
    session, _ = make_session(
        content=b"<html>not video</html>",
        content_type="text/html",
    )

    with pytest.raises(VideoDownloadError):
        download_mp4(
            VIDEO_URL,
            session=session,
        )


def test_invalid_mp4_rejected():
    session, _ = make_session(
        content=b"not an mp4"
    )

    with pytest.raises(VideoDownloadError):
        download_mp4(
            VIDEO_URL,
            session=session,
        )


def test_network_timeout():
    session = MagicMock()
    session.get.side_effect = requests.Timeout()

    with pytest.raises(VideoDownloadError) as exc:
        download_mp4(
            VIDEO_URL,
            session=session,
        )

    assert exc.value.retryable is True


def test_server_error_retryable():
    session, _ = make_session(status=503)

    with pytest.raises(VideoDownloadError) as exc:
        download_mp4(
            VIDEO_URL,
            session=session,
        )

    assert exc.value.retryable is True


def test_invalid_size_limit():
    with pytest.raises(ValueError):
        download_mp4(
            VIDEO_URL,
            session=MagicMock(),
            max_bytes=0,
        )

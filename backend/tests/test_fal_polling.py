from unittest.mock import MagicMock

import pytest

from fal_polling import (
    FalGenerationError,
    get_completed_video,
    poll_until_complete,
    submit_and_wait,
    validate_polling_config,
)
from provider_errors import (
    ProviderTimeoutError,
    RenderProviderError,
)


SUBMISSION = {
    "request_id": "test-request",
    "status_url": (
        "https://queue.fal.run/example/video/"
        "requests/test-request/status"
    ),
    "response_url": (
        "https://queue.fal.run/example/video/"
        "requests/test-request"
    ),
}


def test_successful_polling():
    client = MagicMock()
    client.status.side_effect = [
        {"status": "IN_QUEUE"},
        {"status": "IN_PROGRESS"},
        {"status": "COMPLETED"},
    ]

    result = poll_until_complete(
        client,
        SUBMISSION,
        sleeper=lambda seconds: None,
        clock=lambda: 0,
    )

    assert result["status"] == "COMPLETED"
    assert client.status.call_count == 3


def test_completed_generation_error():
    client = MagicMock()
    client.status.return_value = {
        "status": "COMPLETED",
        "error": "Model execution failed",
    }

    with pytest.raises(FalGenerationError):
        poll_until_complete(
            client,
            SUBMISSION,
            clock=lambda: 0,
        )


def test_completed_error_type():
    client = MagicMock()
    client.status.return_value = {
        "status": "COMPLETED",
        "error_type": "request_timeout",
    }

    with pytest.raises(FalGenerationError):
        poll_until_complete(
            client,
            SUBMISSION,
            clock=lambda: 0,
        )


def test_timeout():
    client = MagicMock()
    client.status.return_value = {
        "status": "IN_PROGRESS"
    }

    times = iter([0, 0, 2, 5, 11])

    with pytest.raises(ProviderTimeoutError):
        poll_until_complete(
            client,
            SUBMISSION,
            timeout_seconds=10,
            poll_interval_seconds=2,
            clock=lambda: next(times),
            sleeper=lambda seconds: None,
        )


def test_unexpected_status():
    client = MagicMock()
    client.status.return_value = {
        "status": "UNKNOWN"
    }

    with pytest.raises(RenderProviderError):
        poll_until_complete(
            client,
            SUBMISSION,
            clock=lambda: 0,
        )


def test_missing_urls():
    client = MagicMock()

    with pytest.raises(RenderProviderError):
        poll_until_complete(
            client,
            {"request_id": "test"},
        )

    client.status.assert_not_called()


def test_untrusted_url():
    client = MagicMock()
    submission = {
        **SUBMISSION,
        "status_url": "http://127.0.0.1/admin",
    }

    with pytest.raises(ValueError):
        poll_until_complete(
            client,
            submission,
        )

    client.status.assert_not_called()


def test_valid_configuration():
    validate_polling_config(900, 10)


def test_invalid_configuration():
    with pytest.raises(ValueError):
        validate_polling_config(0, 10)

    with pytest.raises(ValueError):
        validate_polling_config(900, 0)


def test_get_completed_video():
    client = MagicMock()
    client.result.return_value = {
        "video": {
            "url": "https://fal.media/files/test.mp4"
        }
    }

    completion = {
        "status": "COMPLETED",
        "request_id": "test-request",
        "response_url": SUBMISSION["response_url"],
    }

    result = get_completed_video(
        client,
        completion,
    )

    assert result["video_url"] == (
        "https://fal.media/files/test.mp4"
    )


def test_incomplete_result_rejected():
    with pytest.raises(ValueError):
        get_completed_video(
            MagicMock(),
            {"status": "IN_PROGRESS"},
        )


def test_submit_and_wait():
    client = MagicMock()
    client.submit.return_value = SUBMISSION
    client.status.return_value = {
        "status": "COMPLETED"
    }
    client.result.return_value = {
        "video": {
            "url": "https://fal.media/files/test.mp4"
        }
    }

    result = submit_and_wait(
        client,
        "Cinematic mountain sunset",
        clock=lambda: 0,
        sleeper=lambda seconds: None,
    )

    assert result["status"] == "COMPLETED"

    client.submit.assert_called_once_with(
        "Cinematic mountain sunset"
    )

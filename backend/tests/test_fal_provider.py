from unittest.mock import MagicMock, patch

import pytest
import requests

from fal_provider import (
    FalQueueClient,
    extract_video_url,
    validate_model_id,
    validate_queue_url,
)
from provider_errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RenderProviderError,
)


MODEL = "example/video-model/text-to-video"

STATUS_URL = (
    "https://queue.fal.run/example/video-model/"
    "requests/test-request/status"
)

RESULT_URL = (
    "https://queue.fal.run/example/video-model/"
    "requests/test-request"
)


def make_client(payload=None, status_code=200):
    session = MagicMock()
    response = MagicMock()

    response.status_code = status_code
    response.json.return_value = payload or {
        "request_id": "test-request",
        "status": "IN_QUEUE",
        "status_url": STATUS_URL,
        "response_url": RESULT_URL,
    }

    session.request.return_value = response

    client = FalQueueClient(
        model_id=MODEL,
        api_key="test-placeholder",
        session=session,
    )

    return client, session


def test_valid_model_id():
    assert validate_model_id(MODEL) == MODEL


def test_invalid_model_id():
    with pytest.raises(ValueError):
        validate_model_id("https://example.com/model")


def test_valid_queue_url():
    assert validate_queue_url(STATUS_URL) == STATUS_URL


def test_untrusted_queue_url():
    with pytest.raises(ValueError):
        validate_queue_url("http://127.0.0.1/admin")


def test_lookalike_domain_rejected():
    with pytest.raises(ValueError):
        validate_queue_url(
            "https://queue.fal.run.attacker.example/api"
        )


def test_submit():
    client, session = make_client()

    result = client.submit("Cinematic mountain sunset")

    assert result["request_id"] == "test-request"

    args = session.request.call_args

    assert args.args[0] == "POST"
    assert args.kwargs["json"] == {
        "prompt": "Cinematic mountain sunset"
    }


def test_status():
    client, session = make_client()

    client.status(STATUS_URL)

    assert session.request.call_args.args[:2] == (
        "GET",
        STATUS_URL,
    )


def test_result():
    client, session = make_client()

    client.result(RESULT_URL)

    assert session.request.call_args.args[:2] == (
        "GET",
        RESULT_URL,
    )


def test_rate_limit():
    client, _ = make_client(status_code=429)

    with pytest.raises(ProviderRateLimitError):
        client.submit("Forest sunset")


def test_authentication_failure():
    client, _ = make_client(status_code=401)

    with pytest.raises(ProviderAuthenticationError):
        client.submit("Forest sunset")


def test_network_timeout():
    client, session = make_client()
    session.request.side_effect = requests.Timeout()

    with pytest.raises(ProviderTimeoutError):
        client.submit("Forest sunset")


def test_missing_request_id():
    client, _ = make_client(
        payload={"status": "IN_QUEUE"}
    )

    with pytest.raises(RenderProviderError):
        client.submit("Forest sunset")


def test_extract_video_url():
    result = {
        "video": {
            "url": "https://fal.media/files/example.mp4"
        }
    }

    assert extract_video_url(result) == (
        "https://fal.media/files/example.mp4"
    )


def test_missing_video():
    with pytest.raises(RenderProviderError):
        extract_video_url({})


def test_no_real_network_requests():
    with patch("requests.Session") as session:
        FalQueueClient(
            model_id=MODEL,
            api_key="test-placeholder",
        )

        session.return_value.request.assert_not_called()

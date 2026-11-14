import pytest

from provider_errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RenderProviderError,
    classify_http_status,
)
from provider_execution import execute_render
from render_request import RenderRequest
from video_provider import VideoProviderError


TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def test_successful_render():
    request = RenderRequest(prompt="Ocean sunrise")

    output = execute_render(
        request,
        lambda request: TEST_MP4,
    )

    assert output == TEST_MP4


def test_invalid_video_rejected():
    request = RenderRequest(prompt="Ocean sunrise")

    with pytest.raises(VideoProviderError):
        execute_render(
            request,
            lambda request: b"not-an-mp4",
        )


def test_renderer_receives_request():
    request = RenderRequest(
        prompt="Clouds",
        duration_seconds=8,
    )

    received = []

    def renderer(value):
        received.append(value)
        return TEST_MP4

    execute_render(request, renderer)

    assert received == [request]


def test_invalid_renderer_rejected():
    with pytest.raises(TypeError):
        execute_render(
            RenderRequest(prompt="Test"),
            None,
        )


def test_rate_limit_classification():
    error = classify_http_status(429)

    assert isinstance(error, ProviderRateLimitError)
    assert error.retryable is True


def test_timeout_classification():
    error = classify_http_status(504)

    assert isinstance(error, ProviderTimeoutError)
    assert error.retryable is True


def test_authentication_classification():
    error = classify_http_status(401)

    assert isinstance(
        error,
        ProviderAuthenticationError,
    )

    assert error.retryable is False


def test_server_error_classification():
    error = classify_http_status(503)

    assert isinstance(error, RenderProviderError)
    assert error.retryable is True


def test_provider_error_propagates():
    request = RenderRequest(prompt="Sunset")

    def renderer(request):
        raise ProviderRateLimitError()

    with pytest.raises(ProviderRateLimitError):
        execute_render(request, renderer)

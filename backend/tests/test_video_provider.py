from unittest.mock import patch

import pytest

from provider_config import (
    ProviderConfigurationError,
    validate_provider_config,
)
from video_provider import (
    VideoProviderError,
    get_renderer,
    render_video,
    validate_mp4,
    validate_prompt,
)


VALID_TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def test_provider_disabled_by_default():
    with patch.dict(
        "os.environ",
        {"VIDEO_RENDER_PROVIDER": "disabled"},
    ):
        result = validate_provider_config()

    assert result["enabled"] is False
    assert result["provider"] == "disabled"


def test_unknown_provider_rejected():
    with patch.dict(
        "os.environ",
        {"VIDEO_RENDER_PROVIDER": "unknown"},
    ):
        with pytest.raises(ProviderConfigurationError):
            validate_provider_config()


def test_missing_provider_cannot_render():
    with patch.dict(
        "os.environ",
        {"VIDEO_RENDER_PROVIDER": "disabled"},
    ):
        with pytest.raises(ProviderConfigurationError):
            get_renderer()


def test_valid_prompt():
    assert validate_prompt("  Cinematic sunset  ") == (
        "Cinematic sunset"
    )


def test_empty_prompt_rejected():
    with pytest.raises(ValueError):
        validate_prompt("  ")


def test_invalid_prompt_type():
    with pytest.raises(ValueError):
        validate_prompt(None)


def test_prompt_length_limit():
    with pytest.raises(ValueError):
        validate_prompt("a" * 2001)


def test_mp4_signature_validation():
    assert validate_mp4(VALID_TEST_MP4) == VALID_TEST_MP4


def test_invalid_video_rejected():
    with pytest.raises(VideoProviderError):
        validate_mp4(b"not a video")


def test_renderer_adapter():
    received = []

    def renderer(prompt):
        received.append(prompt)
        return VALID_TEST_MP4

    result = render_video(
        "  Ocean sunrise  ",
        renderer=renderer,
    )

    assert result == VALID_TEST_MP4
    assert received == ["Ocean sunrise"]


def test_renderer_failure_propagates():
    def renderer(prompt):
        raise VideoProviderError("Provider unavailable")

    with pytest.raises(VideoProviderError):
        render_video(
            "Cinematic forest",
            renderer=renderer,
        )

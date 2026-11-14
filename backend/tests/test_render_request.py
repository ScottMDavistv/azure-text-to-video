import pytest

from render_request import RenderRequest


def test_default_request():
    request = RenderRequest(
        prompt="Cinematic mountain sunset"
    )

    assert request.duration_seconds == 5
    assert request.aspect_ratio == "16:9"


def test_prompt_is_trimmed():
    request = RenderRequest(
        prompt="  Ocean waves  "
    )

    assert request.prompt == "Ocean waves"


def test_vertical_video():
    request = RenderRequest(
        prompt="Portrait video",
        duration_seconds=10,
        aspect_ratio="9:16",
    )

    assert request.aspect_ratio == "9:16"


def test_empty_prompt_rejected():
    with pytest.raises(ValueError):
        RenderRequest(prompt="   ")


def test_invalid_duration_rejected():
    with pytest.raises(ValueError):
        RenderRequest(
            prompt="Test",
            duration_seconds=60,
        )


def test_boolean_duration_rejected():
    with pytest.raises(ValueError):
        RenderRequest(
            prompt="Test",
            duration_seconds=True,
        )


def test_invalid_ratio_rejected():
    with pytest.raises(ValueError):
        RenderRequest(
            prompt="Test",
            aspect_ratio="4:3",
        )


def test_request_serialization():
    request = RenderRequest(
        prompt="Forest",
        duration_seconds=5,
    )

    assert request.to_dict()["prompt"] == "Forest"

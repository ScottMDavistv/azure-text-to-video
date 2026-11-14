"""
Interface for AI video-generation providers.

Provider implementations must return valid MP4 bytes.
No live provider is configured yet.
"""

from typing import Protocol, runtime_checkable

from provider_config import (
    ProviderConfigurationError,
    get_provider_name,
)


class VideoProviderError(RuntimeError):
    pass


@runtime_checkable
class VideoRenderer(Protocol):
    def __call__(self, prompt: str) -> bytes:
        ...


def validate_prompt(prompt):
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be a string")

    prompt = prompt.strip()

    if not prompt:
        raise ValueError("Prompt cannot be empty")

    if len(prompt) > 2000:
        raise ValueError("Prompt exceeds 2000 characters")

    return prompt


def validate_mp4(data):
    if not isinstance(data, bytes):
        raise VideoProviderError(
            "Renderer must return bytes"
        )

    # MP4 files normally contain the ftyp box near
    # the start of the file.
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise VideoProviderError(
            "Renderer output does not appear to be an MP4"
        )

    return data


def get_renderer():
    provider = get_provider_name()

    if provider == "disabled":
        raise ProviderConfigurationError(
            "No video-generation provider configured"
        )

    raise ProviderConfigurationError(
        f"Unsupported video provider: {provider}"
    )


def render_video(prompt, renderer=None):
    prompt = validate_prompt(prompt)

    if renderer is None:
        renderer = get_renderer()

    if not callable(renderer):
        raise VideoProviderError(
            "Video renderer must be callable"
        )

    return validate_mp4(renderer(prompt))

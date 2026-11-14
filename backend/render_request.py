"""
Provider-independent video generation request.

Actual provider-specific payload construction is implemented
only after a rendering provider is selected.
"""

from dataclasses import dataclass


SUPPORTED_ASPECT_RATIOS = {
    "16:9",
    "9:16",
    "1:1",
}


@dataclass(frozen=True)
class RenderRequest:
    prompt: str
    duration_seconds: int = 5
    aspect_ratio: str = "16:9"

    def __post_init__(self):
        from video_provider import validate_prompt

        cleaned_prompt = validate_prompt(self.prompt)

        if isinstance(self.duration_seconds, bool):
            raise ValueError("Invalid video duration")

        if not isinstance(self.duration_seconds, int):
            raise ValueError("Duration must be an integer")

        if not 1 <= self.duration_seconds <= 30:
            raise ValueError(
                "Duration must be between 1 and 30 seconds"
            )

        if self.aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
            raise ValueError("Unsupported aspect ratio")

        object.__setattr__(
            self,
            "prompt",
            cleaned_prompt,
        )

    def to_dict(self):
        return {
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
        }

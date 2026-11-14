"""
Pure retry-policy helpers.

These functions do not automatically retry paid generation.

Do not automatically resubmit a Fal request merely because
a network response was lost. That can cause duplicate charges.
"""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    initial_delay: float = 2.0
    max_delay: float = 60.0

    def __post_init__(self):
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
            or self.max_attempts > 20
        ):
            raise ValueError(
                "max_attempts must be between 1 and 20"
            )

        for name in ("initial_delay", "max_delay"):
            value = getattr(self, name)

            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    f"{name} must be finite and positive"
                )

        if self.max_delay < self.initial_delay:
            raise ValueError(
                "max_delay cannot be smaller than initial_delay"
            )


def retry_delay(attempt_number, policy=None):
    policy = policy or RetryPolicy()

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
        or attempt_number >= policy.max_attempts
    ):
        raise ValueError(
            "No retry allowed for this attempt"
        )

    return min(
        policy.initial_delay * (2 ** (attempt_number - 1)),
        policy.max_delay,
    )


def should_retry(error, attempt_number, policy=None):
    policy = policy or RetryPolicy()

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 1
        or attempt_number >= policy.max_attempts
    ):
        return False

    return bool(getattr(error, "retryable", False))

"""
Fal video-generation request polling.

No external requests occur unless these functions
are explicitly called with a configured Fal client.
"""

import time

from fal_provider import extract_video_url
from provider_errors import (
    ProviderTimeoutError,
    RenderProviderError,
)


class FalGenerationError(RenderProviderError):
    def __init__(self, message="Fal generation failed"):
        super().__init__(
            message,
            retryable=False,
            error_code="fal_generation_failed",
        )


def validate_polling_config(
    timeout_seconds,
    poll_interval_seconds,
):
    if isinstance(timeout_seconds, bool):
        raise ValueError("Invalid polling timeout")

    if isinstance(poll_interval_seconds, bool):
        raise ValueError("Invalid polling interval")

    if not isinstance(timeout_seconds, (int, float)):
        raise ValueError("Polling timeout must be numeric")

    if not isinstance(poll_interval_seconds, (int, float)):
        raise ValueError("Polling interval must be numeric")

    if timeout_seconds <= 0:
        raise ValueError("Polling timeout must be positive")

    if poll_interval_seconds <= 0:
        raise ValueError("Polling interval must be positive")


def poll_until_complete(
    client,
    submission,
    *,
    timeout_seconds=900,
    poll_interval_seconds=10,
    clock=time.monotonic,
    sleeper=time.sleep,
):
    """
    Poll Fal until completion.

    A completed request may still report a provider error.
    Both status_url and response_url must come from
    the original Fal submission response.
    """
    validate_polling_config(
        timeout_seconds,
        poll_interval_seconds,
    )

    if not isinstance(submission, dict):
        raise ValueError("Submission must be a dictionary")

    status_url = submission.get("status_url")
    response_url = submission.get("response_url")

    if not status_url or not response_url:
        raise RenderProviderError(
            "Fal submission missing polling URLs",
            error_code="provider_invalid_response",
        )

    # Validate both before making any request.
    from fal_provider import validate_queue_url
    validate_queue_url(status_url)
    validate_queue_url(response_url)

    deadline = clock() + timeout_seconds

    while True:
        if clock() >= deadline:
            raise ProviderTimeoutError(
                "Fal generation exceeded polling timeout"
            )

        status = client.status(status_url)

        if not isinstance(status, dict):
            raise RenderProviderError(
                "Invalid Fal status response",
                error_code="provider_invalid_response",
            )

        state = status.get("status")

        if state == "COMPLETED":
            if status.get("error") or status.get("error_type"):
                raise FalGenerationError(
                    "Fal reported a generation failure"
                )

            return {
                "status": "COMPLETED",
                "request_id": submission.get("request_id"),
                "response_url": response_url,
            }

        if state not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RenderProviderError(
                f"Unexpected Fal request state: {state}",
                error_code="provider_invalid_response",
            )

        remaining = deadline - clock()

        if remaining <= 0:
            raise ProviderTimeoutError(
                "Fal generation exceeded polling timeout"
            )

        sleeper(min(poll_interval_seconds, remaining))


def get_completed_video(client, completion):
    """
    Retrieve the completed Fal result and return
    the video's HTTPS URL without downloading it.
    """
    if not isinstance(completion, dict):
        raise ValueError("Completion must be a dictionary")

    if completion.get("status") != "COMPLETED":
        raise ValueError("Fal request is not completed")

    response_url = completion.get("response_url")

    if not response_url:
        raise ValueError("Missing Fal response URL")

    result = client.result(response_url)
    video_url = extract_video_url(result)

    return {
        "request_id": completion.get("request_id"),
        "video_url": video_url,
        "status": "COMPLETED",
    }


def submit_and_wait(
    client,
    prompt,
    *,
    timeout_seconds=900,
    poll_interval_seconds=10,
    clock=time.monotonic,
    sleeper=time.sleep,
):
    """
    Submit a request, wait, then retrieve its video URL.

    Does not download or store the video.
    """
    submission = client.submit(prompt)

    completion = poll_until_complete(
        client,
        submission,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        clock=clock,
        sleeper=sleeper,
    )

    return get_completed_video(client, completion)

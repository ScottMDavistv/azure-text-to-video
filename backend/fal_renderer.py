"""
Fal text-to-video rendering adapter.

Combines Fal submission, polling, and secure MP4 download.

Constructing the adapter does not make network requests.
Real rendering requires explicit invocation with a configured client.
"""

from fal_polling import submit_and_wait
from video_download import download_mp4
from video_provider import validate_mp4, validate_prompt


class FalVideoRenderer:
    def __init__(
        self,
        client,
        *,
        downloader=None,
        timeout_seconds=900,
        poll_interval_seconds=10,
    ):
        if client is None:
            raise ValueError("Fal client is required")

        if timeout_seconds <= 0:
            raise ValueError("Timeout must be positive")

        if poll_interval_seconds <= 0:
            raise ValueError("Polling interval must be positive")

        self.client = client
        self.downloader = (
            downloader
            if downloader is not None
            else download_mp4
        )
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def __call__(self, prompt):
        # This prompt-only adapter has no job identity, project membership,
        # reservation or durable submission intent. It must never POST to FAL.
        raise RuntimeError('Legacy FAL renderer disabled: use budgeted job execution')

        result = submit_and_wait(
            self.client,
            prompt,
            timeout_seconds=self.timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        )

        video_url = result["video_url"]

        video_bytes = self.downloader(video_url)

        return validate_mp4(video_bytes)

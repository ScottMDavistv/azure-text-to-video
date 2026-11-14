"""
Fal queue API client.

No requests are made unless its methods are explicitly called.
No default model or real API key is configured.
"""

import os
import re
from urllib.parse import urlparse

import requests

from provider_credentials import get_provider_credential
from provider_errors import (
    RenderProviderError,
    classify_http_status,
)
from video_provider import validate_prompt


MODEL_PATTERN = re.compile(
    r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$"
)

QUEUE_HOST = "queue.fal.run"


def validate_model_id(model_id):
    if not isinstance(model_id, str):
        raise ValueError("Fal model ID must be a string")

    model_id = model_id.strip()

    if not MODEL_PATTERN.fullmatch(model_id):
        raise ValueError("Invalid Fal model ID")

    return model_id


def validate_queue_url(url):
    """
    Accept only HTTPS URLs hosted by queue.fal.run.

    Prevent callers from passing arbitrary internal URLs.
    """
    if not isinstance(url, str):
        raise ValueError("Invalid queue URL")

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != QUEUE_HOST
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Untrusted Fal queue URL")

    return url


class FalQueueClient:
    def __init__(
        self,
        model_id=None,
        api_key=None,
        session=None,
        timeout=30,
    ):
        selected = model_id or os.getenv("FAL_MODEL_ID", "")
        self.model_id = validate_model_id(selected)

        self.api_key = (
            api_key
            if api_key is not None
            else get_provider_credential("fal")
        )

        if not self.api_key:
            raise ValueError("Fal API key is required")

        self.session = session or requests.Session()
        self.timeout = timeout

        if timeout <= 0:
            raise ValueError("Timeout must be positive")

    def _headers(self):
        return {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method, url, **kwargs):
        validate_queue_url(url)

        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout,
                allow_redirects=False,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise classify_http_status(504) from exc
        except requests.RequestException as exc:
            raise RenderProviderError(
                "Fal network request failed",
                retryable=True,
                error_code="provider_network",
            ) from exc

        if response.status_code >= 400:
            raise classify_http_status(response.status_code)

        if not 200 <= response.status_code < 300:
            raise RenderProviderError(
                "Unexpected Fal response status",
                error_code="provider_http_error",
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise RenderProviderError(
                "Fal returned invalid JSON",
                error_code="provider_invalid_response",
            ) from exc

        if not isinstance(result, dict):
            raise RenderProviderError(
                "Fal response must be a JSON object",
                error_code="provider_invalid_response",
            )

        return result

    def submit(self, prompt):
        prompt = validate_prompt(prompt)

        url = f"https://{QUEUE_HOST}/{self.model_id}"

        result = self._request(
            "POST",
            url,
            json={"prompt": prompt},
        )

        if not result.get("request_id"):
            raise RenderProviderError(
                "Fal submission did not return a request ID",
                error_code="provider_invalid_response",
            )

        for key in ("status_url", "response_url"):
            if result.get(key):
                validate_queue_url(result[key])

        return result

    def status(self, status_url):
        return self._request(
            "GET",
            validate_queue_url(status_url),
        )

    def result(self, response_url):
        return self._request(
            "GET",
            validate_queue_url(response_url),
        )


def extract_video_url(result):
    """
    Extract a video URL without downloading it.

    Result schemas vary between models. This supports
    the common Fal result.video.url structure.
    """
    if not isinstance(result, dict):
        raise ValueError("Video result must be a JSON object")

    video = result.get("video")

    if not isinstance(video, dict):
        raise RenderProviderError(
            "Video result missing video object",
            error_code="provider_invalid_response",
        )

    url = video.get("url")

    if not isinstance(url, str):
        raise RenderProviderError(
            "Video result missing URL",
            error_code="provider_invalid_response",
        )

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise RenderProviderError(
            "Invalid video result URL",
            error_code="provider_invalid_response",
        )

    return url

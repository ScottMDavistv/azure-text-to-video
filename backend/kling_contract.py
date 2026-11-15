"""Kling 3.0 Pro first-test contract. No network calls."""

from decimal import Decimal
from urllib.parse import urlsplit

MODEL_ID = "fal-ai/kling-video/v3/pro/text-to-video"
MAX_VIDEO_BYTES = 100 * 1024 * 1024
BUDGET_USD = Decimal("5.00")
ESTIMATED_RATE = Decimal("0.14")


class ContractError(ValueError):
    pass


def first_test_request(prompt):
    if not isinstance(prompt, str):
        raise ContractError("Prompt must be text")

    prompt = prompt.strip()

    if not 1 <= len(prompt) <= 2500:
        raise ContractError("Invalid prompt length")

    return {
        "prompt": prompt,
        "duration": "5",
        "aspect_ratio": "16:9",
        "generate_audio": False,
        "enable_safety_checker": True,
        "shot_type": "customize",
        "cfg_scale": 0.5,
    }


def validate_request(data):
    if not isinstance(data, dict):
        raise ContractError("Expected object")

    required = {
        "prompt", "duration", "aspect_ratio",
        "generate_audio", "enable_safety_checker",
        "shot_type", "cfg_scale",
    }

    if set(data) != required:
        raise ContractError("Unexpected request fields")

    if not isinstance(data["prompt"], str):
        raise ContractError("Invalid prompt")

    if not 1 <= len(data["prompt"].strip()) <= 2500:
        raise ContractError("Invalid prompt length")

    duration = data["duration"]

    if type(duration) is not str or duration != "5":
        raise ContractError("First test must be five seconds")

    if data["aspect_ratio"] != "16:9":
        raise ContractError("First test must use 16:9")

    if data["generate_audio"] is not False:
        raise ContractError("Audio must be disabled")

    if data["enable_safety_checker"] is not True:
        raise ContractError("Safety checker must remain enabled")

    if data["shot_type"] != "customize":
        raise ContractError("Unsupported shot type")

    if (
        type(data["cfg_scale"]) not in (int, float)
        or data["cfg_scale"] != 0.5
    ):
        raise ContractError("Invalid CFG scale")

    return data


def estimated_cost(data):
    validate_request(data)
    return Decimal(data["duration"]) * ESTIMATED_RATE


def validate_response(result):
    if not isinstance(result, dict):
        raise ContractError("Expected result object")

    video = result.get("video")

    if not isinstance(video, dict):
        raise ContractError("Missing video object")

    url = video.get("url")

    if not isinstance(url, str):
        raise ContractError("Missing video URL")

    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContractError("Invalid URL") from exc

    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ContractError("Unsafe URL")

    valid_host = (
        (
            host == "storage.googleapis.com"
            and parsed.path.startswith("/falserverless/")
        )
        or host == "fal.media"
        or host.endswith(".fal.media")
    )

    if not valid_host:
        raise ContractError("Unapproved output host")

    content_type = video.get("content_type", "video/mp4")

    if content_type != "video/mp4":
        raise ContractError("Unsupported content type")

    file_size = video.get("file_size")

    if file_size is not None:
        if (
            type(file_size) is not int
            or not 0 < file_size <= MAX_VIDEO_BYTES
        ):
            raise ContractError("Invalid file size")

    return {
        "url": url,
        "content_type": content_type,
        "file_size": file_size,
    }

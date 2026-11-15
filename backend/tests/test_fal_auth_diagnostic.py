from unittest.mock import Mock, patch
from urllib.parse import urlparse
import uuid

import requests

from fal_auth_diagnostic import (
    check_fal_api,
    diagnostic_exit_code,
)


MODEL = "fal-ai/kling-video/v3/pro/text-to-video"
KEY = "super-secret-placeholder"


def perform(status):
    get = Mock(
        return_value=Mock(status_code=status)
    )

    result = check_fal_api(
        get=get,
        key=KEY,
        model_id=MODEL,
    )

    return result, get


def test_404_is_reachable_but_inconclusive():
    result, _ = perform(404)

    assert result["outcome"] == (
        "reachable_auth_inconclusive"
    )
    assert result["authentication_verified"] is False
    assert result["model_access_verified"] is False
    assert diagnostic_exit_code(result) == 0


def test_401_rejected():
    result, _ = perform(401)

    assert result["outcome"] == (
        "authentication_rejected"
    )
    assert diagnostic_exit_code(result) == 3


def test_403_denied():
    result, _ = perform(403)

    assert result["outcome"] == "access_denied"
    assert diagnostic_exit_code(result) == 3


def test_429_rate_limited():
    result, _ = perform(429)

    assert result["outcome"] == "rate_limited"
    assert diagnostic_exit_code(result) == 4


def test_500_server_error():
    result, _ = perform(500)

    assert result["outcome"] == (
        "provider_server_error"
    )


def test_redirect_rejected():
    result, get = perform(302)

    assert result["outcome"] == (
        "unexpected_redirect"
    )
    assert get.call_args.kwargs[
        "allow_redirects"
    ] is False


def test_no_credential_means_no_request():
    get = Mock()

    result = check_fal_api(
        get=get,
        key="",
        model_id=MODEL,
    )

    get.assert_not_called()
    assert result["outcome"] == "missing_credential"


def test_invalid_model_means_no_request():
    get = Mock()

    result = check_fal_api(
        get=get,
        key=KEY,
        model_id="https://example.com/evil",
    )

    get.assert_not_called()
    assert result["outcome"] == (
        "invalid_model_identifier"
    )


def test_correct_queue_status_endpoint():
    _, get = perform(404)

    args, kwargs = get.call_args
    parsed = urlparse(args[0])

    assert parsed.scheme == "https"
    assert parsed.netloc == "queue.fal.run"

    segments = parsed.path.strip("/").split("/")

    assert segments[:3] == [
        "fal-ai",
        "kling-video",
        "requests",
    ]

    assert segments[-1] == "status"

    uuid.UUID(segments[-2])

    assert kwargs["timeout"] == (5, 15)
    assert kwargs["headers"]["Authorization"] == (
        f"Key {KEY}"
    )


def test_distinct_fake_ids_each_time():
    _, first = perform(404)
    _, second = perform(404)

    assert first.call_args.args[0] != (
        second.call_args.args[0]
    )


def test_secret_not_in_results():
    result, _ = perform(404)

    assert KEY not in str(result)
    assert result["request_submitted"] is False
    assert result["generation_started"] is False


def test_network_failure_safe_output():
    get = Mock(
        side_effect=requests.Timeout(
            "secret must not be printed"
        )
    )

    result = check_fal_api(
        get=get,
        key=KEY,
        model_id=MODEL,
    )

    assert result["outcome"] == "network_error"
    assert KEY not in str(result)
    assert "secret must not be printed" not in str(result)


def test_worker_cli_diagnostic_mode(capsys):
    from worker_runtime import main

    with patch(
        "fal_auth_diagnostic.check_fal_api",
        return_value={
            "outcome": "reachable_auth_inconclusive",
            "http_status": 404,
            "request_submitted": False,
        },
    ):
        result = main(["--verify-fal"])

    assert result == 0

    output = capsys.readouterr().out
    assert '"http_status": 404' in output


def test_worker_diagnostic_never_starts_polling():
    from worker_runtime import main

    with (
        patch(
            "fal_auth_diagnostic.check_fal_api",
            return_value={
                "outcome": "reachable_auth_inconclusive",
                "http_status": 404,
                "request_submitted": False,
            },
        ),
        patch("worker_runtime.run_worker") as worker,
    ):
        assert main(["--verify-fal"]) == 0
        worker.assert_not_called()

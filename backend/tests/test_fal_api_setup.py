from unittest.mock import patch

from fal_api_setup import (
    fal_configuration_status,
    main,
)


def test_no_key_is_not_leaked():
    with patch.dict(
        "os.environ",
        {
            "FAL_KEY": "super-secret-test-value",
            "FAL_MODEL_ID": (
                "fal-ai/kling-video/v3/pro/text-to-video"
            ),
        },
        clear=True,
    ):
        result = fal_configuration_status()

    assert result["api_key_configured"] is True
    assert "super-secret-test-value" not in str(result)


def test_model_identifier():
    with patch.dict(
        "os.environ",
        {
            "FAL_MODEL_ID": (
                "fal-ai/kling-video/v3/pro/text-to-video"
            ),
        },
        clear=True,
    ):
        result = fal_configuration_status()

    assert result["model_id_valid"] is True


def test_invalid_model_identifier():
    with patch.dict(
        "os.environ",
        {"FAL_MODEL_ID": "https://malicious.example"},
        clear=True,
    ):
        result = fal_configuration_status()

    assert result["model_id_valid"] is False


def test_defaults_do_not_enable_rendering():
    with patch.dict(
        "os.environ",
        {},
        clear=True,
    ):
        result = fal_configuration_status()

    assert result["live_rendering_enabled"] is False
    assert result["worker_consumption_enabled"] is False
    assert result["billable_request_submitted"] is False


def test_check_command(capsys):
    assert main(["--check"]) == 0
    output = capsys.readouterr().out

    assert '"provider": "fal"' in output
    assert '"billable_request_submitted": false' in output

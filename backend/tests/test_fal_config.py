from unittest.mock import patch

from fal_config import check_fal_configuration


def test_unconfigured_fal():
    with patch.dict(
        "os.environ",
        {
            "FAL_MODEL_ID": "",
            "FAL_KEY": "",
        },
    ):
        result = check_fal_configuration()

    assert result["model_configured"] is False
    assert result["credential_configured"] is False
    assert result["live_rendering_enabled"] is False


def test_model_and_credentials_configured():
    with patch.dict(
        "os.environ",
        {
            "FAL_MODEL_ID": "example/video-model",
            "FAL_KEY": "test-placeholder",
        },
    ):
        result = check_fal_configuration()

    assert result["model_configured"] is True
    assert result["credential_configured"] is True
    assert result["live_rendering_enabled"] is False
    assert "test-placeholder" not in str(result)

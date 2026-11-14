from unittest.mock import MagicMock, patch

import pytest

from fal_renderer import FalVideoRenderer
from render_pipeline import (
    RenderingDisabledError,
    create_render_pipeline,
    live_rendering_enabled,
)


def test_rendering_disabled_by_default():
    with patch.dict(
        "os.environ",
        {
            "ENABLE_LIVE_VIDEO_RENDERING": "",
            "VIDEO_RENDER_PROVIDER": "disabled",
        },
    ):
        assert live_rendering_enabled() is False

        with pytest.raises(RenderingDisabledError):
            create_render_pipeline(client=MagicMock())


def test_wrong_provider_rejected():
    with patch.dict(
        "os.environ",
        {
            "ENABLE_LIVE_VIDEO_RENDERING": "true",
            "VIDEO_RENDER_PROVIDER": "disabled",
        },
    ):
        with pytest.raises(RenderingDisabledError):
            create_render_pipeline(client=MagicMock())


def test_explicit_fal_configuration():
    with patch.dict(
        "os.environ",
        {
            "ENABLE_LIVE_VIDEO_RENDERING": "true",
            "VIDEO_RENDER_PROVIDER": "fal",
        },
    ):
        renderer = create_render_pipeline(
            client=MagicMock(),
            downloader=MagicMock(),
        )

    assert isinstance(renderer, FalVideoRenderer)


def test_no_api_calls_during_construction():
    client = MagicMock()

    with patch.dict(
        "os.environ",
        {
            "ENABLE_LIVE_VIDEO_RENDERING": "true",
            "VIDEO_RENDER_PROVIDER": "fal",
        },
    ):
        create_render_pipeline(
            client=client,
            downloader=MagicMock(),
        )

    client.submit.assert_not_called()
    client.status.assert_not_called()
    client.result.assert_not_called()

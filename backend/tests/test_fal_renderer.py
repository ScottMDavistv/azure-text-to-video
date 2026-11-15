from unittest.mock import MagicMock, patch

import pytest

from fal_renderer import FalVideoRenderer


def test_legacy_renderer_never_submits_paid_request():
    client = MagicMock()
    downloader = MagicMock()
    renderer = FalVideoRenderer(client, downloader=downloader)
    with patch("fal_renderer.submit_and_wait") as submit:
        with pytest.raises(RuntimeError, match="Legacy FAL renderer disabled"):
            renderer("Cinematic ocean sunrise")
    submit.assert_not_called()
    client.submit.assert_not_called()
    downloader.assert_not_called()


def test_legacy_renderer_cannot_be_enabled_by_patching_submit():
    client = MagicMock()
    with patch("fal_renderer.submit_and_wait") as submit:
        with pytest.raises(RuntimeError):
            FalVideoRenderer(client)("Mountain sunset")
    submit.assert_not_called()


def test_legacy_renderer_requires_client():
    with pytest.raises(ValueError):
        FalVideoRenderer(None)


def test_legacy_renderer_disables_even_unavailable_provider():
    client = MagicMock()
    client.submit.side_effect = RuntimeError("Provider unavailable")
    with pytest.raises(RuntimeError, match="Legacy FAL renderer disabled"):
        FalVideoRenderer(client)("Cinematic forest")
    client.submit.assert_not_called()

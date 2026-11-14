"""
Build a complete Fal rendering pipeline.

Live execution is disabled unless explicitly enabled.
No network requests occur when constructing the pipeline.
"""

import os

from fal_provider import FalQueueClient
from fal_renderer import FalVideoRenderer
from provider_config import get_provider_name


class RenderingDisabledError(RuntimeError):
    pass


def live_rendering_enabled():
    return (
        os.getenv("ENABLE_LIVE_VIDEO_RENDERING", "")
        .strip()
        .lower()
        in {"1", "true", "yes"}
    )


def create_render_pipeline(
    *,
    client=None,
    downloader=None,
):
    if not live_rendering_enabled():
        raise RenderingDisabledError(
            "Live rendering is disabled"
        )

    if get_provider_name() != "fal":
        raise RenderingDisabledError(
            "Fal is not the selected rendering provider"
        )

    if client is None:
        client = FalQueueClient()

    return FalVideoRenderer(
        client,
        downloader=downloader,
    )

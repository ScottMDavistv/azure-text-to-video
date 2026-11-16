"""
Build a complete Fal rendering pipeline.

Live execution is disabled unless explicitly enabled.
No network requests occur when constructing the pipeline.
"""

import os

from fal_provider import FalQueueClient
from fal_renderer import FalVideoRenderer
from provider_config import get_provider_name, validate_provider_config, ProviderConfigurationError


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

    # This must be checked in the actual construction path, not only
    # by a standalone configuration test. Paid FAL is intentionally
    # unsupported until durable spending enforcement is activated.
    try:
        config = validate_provider_config()
    except ProviderConfigurationError as exc:
        raise RenderingDisabledError(
            "Provider not enabled by application allowlist"
        ) from exc
    if config["provider"] != "fal" or config["enabled"] is not True:
        raise RenderingDisabledError(
            "Fal is not enabled for rendering"
        )

    # An allowed provider name is not authority to spend. The worker's
    # durable ledger-backed reservation gate is deliberately unavailable
    # in this development PR; fail closed even if the allowlist is widened.
    from worker_readiness import require_worker_readiness, WorkerNotReadyError
    try:
        require_worker_readiness()
    except WorkerNotReadyError as exc:
        raise RenderingDisabledError(
            "Durable finance and worker readiness required before paid rendering"
        ) from exc

    if client is None:
        client = FalQueueClient()

    return FalVideoRenderer(
        client,
        downloader=downloader,
    )

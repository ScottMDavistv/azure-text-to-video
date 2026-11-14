"""
Provider-independent execution boundary.

The supplied renderer is injectable for tests.
No actual provider implementation is enabled.
"""

from provider_errors import RenderProviderError
from render_request import RenderRequest
from video_provider import validate_mp4


def execute_render(request, renderer):
    if not isinstance(request, RenderRequest):
        raise TypeError(
            "request must be a RenderRequest"
        )

    if not callable(renderer):
        raise TypeError(
            "renderer must be callable"
        )

    try:
        output = renderer(request)
    except RenderProviderError:
        raise

    # Unexpected exceptions propagate so the worker can
    # record the failure without hiding its cause.
    return validate_mp4(output)

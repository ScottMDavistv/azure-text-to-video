"""
Video-generation provider configuration.

No external rendering provider is enabled by default.
Credentials must come from environment variables or
Azure-managed secrets, never source code.
"""

import os


SUPPORTED_PROVIDERS = {"disabled"}


class ProviderConfigurationError(ValueError):
    pass


def get_provider_name():
    return os.getenv(
        "VIDEO_RENDER_PROVIDER",
        "disabled",
    ).strip().lower()


def validate_provider_config():
    provider = get_provider_name()

    if provider not in SUPPORTED_PROVIDERS:
        raise ProviderConfigurationError(
            f"Unsupported video provider: {provider}"
        )

    return {
        "provider": provider,
        "enabled": provider != "disabled",
        "configured": provider != "disabled",
    }

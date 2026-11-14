"""
Secure video-provider credential configuration.

Credentials are loaded from environment variables.
Never hardcode secrets or print credential values.

This module does not connect to external services.
"""

import os


class MissingProviderCredential(RuntimeError):
    pass


PROVIDER_SECRET_VARIABLES = {
    "fal": "FAL_KEY",
}


def credential_variable_name(provider):
    provider = str(provider).strip().lower()

    if provider not in PROVIDER_SECRET_VARIABLES:
        raise ValueError(
            f"No credential configuration for provider: {provider}"
        )

    return PROVIDER_SECRET_VARIABLES[provider]


def get_provider_credential(provider):
    variable = credential_variable_name(provider)
    credential = os.getenv(variable, "").strip()

    if not credential:
        raise MissingProviderCredential(
            f"Required provider credential is missing: {variable}"
        )

    return credential


def credential_is_configured(provider):
    variable = credential_variable_name(provider)
    return bool(os.getenv(variable, "").strip())


def provider_credential_status(provider):
    """
    Return safe metadata only, never the credential itself.
    """
    return {
        "provider": str(provider).strip().lower(),
        "credential_variable": credential_variable_name(provider),
        "credential_configured": credential_is_configured(provider),
    }

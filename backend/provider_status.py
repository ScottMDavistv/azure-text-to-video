"""
Safe operational status for video-provider configuration.

Never return or log provider API keys.
"""

import os

from provider_config import get_provider_name
from provider_credentials import provider_credential_status


def get_render_configuration_status():
    selected = get_provider_name()

    result = {
        "selected_provider": selected,
        "rendering_enabled": False,
        "integration_status": "not_connected",
    }

    if selected == "disabled":
        result["integration_status"] = "disabled"
        return result

    if selected == "fal":
        result.update(provider_credential_status("fal"))
        result["integration_status"] = (
            "credentials_present_integration_pending"
            if result["credential_configured"]
            else "credentials_missing"
        )
        return result

    result["integration_status"] = "unsupported_provider"
    return result


def check_runtime_environment():
    return {
        "azure_client_id_configured": bool(
            os.getenv("AZURE_CLIENT_ID", "").strip()
        ),
        "storage_account_configured": bool(
            os.getenv("AZURE_STORAGE_ACCOUNT", "").strip()
        ),
        "queue_name": os.getenv(
            "AZURE_JOBS_QUEUE",
            "textvideo-jobs",
        ),
        "provider": get_render_configuration_status(),
    }

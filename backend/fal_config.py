"""
Safe Fal configuration checks.
No API keys are printed.
"""

import os

from fal_provider import validate_model_id
from provider_credentials import credential_is_configured


def check_fal_configuration():
    model_id = os.getenv("FAL_MODEL_ID", "").strip()

    if model_id:
        model_id = validate_model_id(model_id)

    return {
        "provider": "fal",
        "model_id": model_id or None,
        "model_configured": bool(model_id),
        "credential_configured": credential_is_configured("fal"),
        "live_rendering_enabled": False,
    }

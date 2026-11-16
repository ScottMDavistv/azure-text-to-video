"""Dry-run runtime configuration validator.

No real Azure or provider calls. Intended for CI and a future
operator-controlled scheduler job. This module never enables paid FAL.
"""

import os


REQUIRED_FOR_REAL_IDENTITY = (
    "PROJECT_OIDC_ISSUER",
    "PROJECT_OIDC_AUDIENCE",
    "PROJECT_OIDC_JWKS_URL",
)


def validate_safe_defaults(env=None):
    env = os.environ if env is None else env
    prohibited = (
        "ENABLE_PAID_FAL_WORKER",
        "ENABLE_PROJECT_ROUTES",
        "FAL_KEY",
        "FAL_API_KEY",
    )
    for name in prohibited[:2]:
        if env.get(name, "").strip().lower() == "true":
            raise RuntimeError("Unsafe Stage A activation: " + name)
    for name in prohibited[2:]:
        if env.get(name, "").strip():
            raise RuntimeError("Paid credential present during non-billable validation")
    return {"paid_fal_enabled": False, "oidc_configured": all(
        env.get(name) for name in REQUIRED_FOR_REAL_IDENTITY
    ), "mode": "non_billable_validation"}

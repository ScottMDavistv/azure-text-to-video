"""Offline Microsoft Entra ID project-token deployment preflight.

Does not create an app registration, call Microsoft Graph, or enable routes.
Use before setting PROJECT_OIDC_* in an approved deployment.
"""

from urllib.parse import urlparse
from uuid import UUID


class EntraConfigurationError(ValueError):
    pass


def expected_entra_settings(tenant_id, audience):
    try:
        if str(UUID(tenant_id)) != tenant_id:
            raise ValueError("Tenant must be a canonical UUID")
    except (ValueError, TypeError, AttributeError) as exc:
        raise EntraConfigurationError("Verified tenant UUID required") from exc
    if not isinstance(audience, str) or not audience.strip():
        raise EntraConfigurationError("Verified API access-token audience required")
    if len(audience) > 256 or any(ch.isspace() for ch in audience):
        raise EntraConfigurationError("Invalid audience")
    if audience.startswith("api://"):
        if not audience[6:] or "/" in audience[6:]:
            raise EntraConfigurationError("Unexpected application URI")
    else:
        try:
            if str(UUID(audience)) != audience:
                raise ValueError("Expected application client UUID")
        except ValueError as exc:
            raise EntraConfigurationError("Audience must be verified API application ID") from exc
    base = f"https://login.microsoftonline.com/{tenant_id}"
    return {
        "PROJECT_OIDC_ISSUER": base + "/v2.0",
        "PROJECT_OIDC_JWKS_URL": base + "/discovery/v2.0/keys",
        "PROJECT_OIDC_AUDIENCE": audience,
    }


def check_entra_settings(tenant_id, audience, actual):
    """Fail closed on arbitrary issuer, JWKS URLs or audience drift."""
    if not isinstance(actual, dict):
        raise EntraConfigurationError("Settings mapping required")
    expected = expected_entra_settings(tenant_id, audience)
    for key, value in expected.items():
        if actual.get(key) != value:
            raise EntraConfigurationError(f"Mismatched {key}")
    if actual.get("ENABLE_PROJECT_ROUTES", "false").lower() == "true":
        raise EntraConfigurationError("Routes must remain disabled during preflight")
    return {"mode": "preflight_only", "configured": True}

import pytest
from uuid import uuid4

from entra_configuration import (
    EntraConfigurationError, expected_entra_settings, check_entra_settings,
)


def uid():
    return str(uuid4())


def test_entra_settings_are_exact_tenant_urls():
    tenant = uid()
    audience = "api://" + uid()
    settings = expected_entra_settings(tenant, audience)
    assert settings["PROJECT_OIDC_ISSUER"] == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert settings["PROJECT_OIDC_JWKS_URL"] == f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    assert check_entra_settings(tenant, audience, settings)["configured"]


def test_wrong_tenant_or_jwks_rejected():
    tenant = uid()
    audience = "api://" + uid()
    settings = expected_entra_settings(tenant, audience)
    settings["PROJECT_OIDC_JWKS_URL"] = "https://attacker.example/keys"
    with pytest.raises(EntraConfigurationError):
        check_entra_settings(tenant, audience, settings)


def test_cannot_enable_project_routes_during_preflight():
    tenant = uid()
    audience = uid()
    settings = expected_entra_settings(tenant, audience)
    settings["ENABLE_PROJECT_ROUTES"] = "true"
    with pytest.raises(EntraConfigurationError):
        check_entra_settings(tenant, audience, settings)


def test_invalid_tenant_and_audience_rejected():
    with pytest.raises(EntraConfigurationError):
        expected_entra_settings("common", "api://something")
    with pytest.raises(EntraConfigurationError):
        expected_entra_settings(uid(), "https://not-my-api.example")

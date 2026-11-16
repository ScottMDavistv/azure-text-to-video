"""F-06: prove actual application runtime safety, not just workflow variables.

Do not contact the network or Azure and do not initialize paid providers.
"""
import pytest

from provider_config import (
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    get_provider_name,
    validate_provider_config,
)
from worker_readiness import worker_readiness, WorkerNotReadyError, require_worker_readiness


def clear_runtime(monkeypatch):
    for key in (
        "VIDEO_RENDER_PROVIDER", "VIDEO_PROVIDER", "ENABLE_PAID_FAL_WORKER",
        "ENABLE_PROJECT_ROUTES", "ENABLE_WORKER_CONSUMPTION",
        "ENABLE_LIVE_VIDEO_RENDERING", "ACKNOWLEDGE_PROVIDER_BILLING_RISK",
        "FAL_KEY", "FAL_API_KEY", "FAL_MODEL_ID",
        "AZURE_STORAGE_ACCOUNT", "AZURE_JOBS_QUEUE",
        "PROJECT_OIDC_ISSUER", "PROJECT_OIDC_AUDIENCE",
        "PROJECT_OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_actual_provider_is_disabled_when_configuration_absent(monkeypatch):
    clear_runtime(monkeypatch)
    assert SUPPORTED_PROVIDERS == {"disabled"}
    assert get_provider_name() == "disabled"
    assert validate_provider_config() == {
        "provider": "disabled", "enabled": False, "configured": False,
    }


def test_paid_provider_cannot_be_enabled_by_environment_alone(monkeypatch):
    clear_runtime(monkeypatch)
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    with pytest.raises(ProviderConfigurationError):
        validate_provider_config()


def test_worker_not_ready_when_environment_empty(monkeypatch):
    clear_runtime(monkeypatch)
    status = worker_readiness()
    assert status["ready_for_live_execution"] is False
    assert status["durable_cost_enforcement"] is False
    assert status["provider_access_verified"] is False
    assert status["azure_connectivity_verified"] is False
    assert not all(status["checks"].values())
    with pytest.raises(WorkerNotReadyError):
        require_worker_readiness()

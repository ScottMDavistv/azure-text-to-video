"""Backend completion phase: paid-rendering safety contract.

These tests deliberately use mocks only and must never contact FAL or Azure.
A provider allowlist update is not authorization to spend money.
"""
from unittest.mock import Mock

import pytest

import provider_config
import render_pipeline
import worker_readiness


PAID_FLAGS = (
    "ENABLE_LIVE_VIDEO_RENDERING",
    "ENABLE_WORKER_CONSUMPTION",
    "ENABLE_PAID_FAL_WORKER",
    "ACKNOWLEDGE_PROVIDER_BILLING_RISK",
)


@pytest.fixture
def no_paid_side_effects(monkeypatch):
    for key in PAID_FLAGS + ("VIDEO_RENDER_PROVIDER", "FAL_KEY", "FAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    constructor = Mock(side_effect=AssertionError("FAL client must not be created"))
    monkeypatch.setattr(render_pipeline, "FalQueueClient", constructor)
    return constructor


@pytest.mark.parametrize("enabled", ("", "false", "0", "no"))
def test_off_flags_cannot_construct_client(monkeypatch, no_paid_side_effects, enabled):
    monkeypatch.setenv("ENABLE_LIVE_VIDEO_RENDERING", enabled)
    with pytest.raises(render_pipeline.RenderingDisabledError, match="Live rendering"):
        render_pipeline.create_render_pipeline()
    no_paid_side_effects.assert_not_called()


def test_unsupported_provider_cannot_construct_client(monkeypatch, no_paid_side_effects):
    for key in PAID_FLAGS:
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    with pytest.raises(render_pipeline.RenderingDisabledError, match="allowlist"):
        render_pipeline.create_render_pipeline()
    no_paid_side_effects.assert_not_called()


def test_allowlisted_provider_still_requires_durable_finance(
    monkeypatch, no_paid_side_effects
):
    for key in PAID_FLAGS:
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    monkeypatch.setattr(provider_config, "SUPPORTED_PROVIDERS", {"disabled", "fal"})

    # Fail closed even when every operator-controlled switch is turned on.
    assert worker_readiness.worker_readiness()["durable_cost_enforcement"] is False
    with pytest.raises(render_pipeline.RenderingDisabledError, match="Durable finance"):
        render_pipeline.create_render_pipeline()
    no_paid_side_effects.assert_not_called()


def test_client_injection_is_not_an_authorization_bypass(
    monkeypatch, no_paid_side_effects
):
    for key in PAID_FLAGS:
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    monkeypatch.setattr(provider_config, "SUPPORTED_PROVIDERS", {"disabled", "fal"})
    injected_client = Mock()
    with pytest.raises(render_pipeline.RenderingDisabledError, match="Durable finance"):
        render_pipeline.create_render_pipeline(client=injected_client)
    injected_client.submit.assert_not_called()
    no_paid_side_effects.assert_not_called()

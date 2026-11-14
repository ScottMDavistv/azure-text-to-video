from unittest.mock import patch

import pytest

from worker_readiness import (
    WorkerNotReadyError,
    env_enabled,
    require_worker_readiness,
    worker_readiness,
)


READY_ENV = {
    "ENABLE_WORKER_CONSUMPTION": "true",
    "ENABLE_LIVE_VIDEO_RENDERING": "true",
    "ACKNOWLEDGE_PROVIDER_BILLING_RISK": "true",
    "VIDEO_RENDER_PROVIDER": "fal",
    "FAL_MODEL_ID": "example/video-model",
    "FAL_KEY": "test-placeholder",
    "AZURE_STORAGE_ACCOUNT": "teststorage",
    "AZURE_JOBS_QUEUE": "textvideo-jobs",
    "VIDEO_PER_JOB_LIMIT_USD": "2",
    "VIDEO_MONTHLY_LIMIT_USD": "50",
    "VIDEO_ESTIMATED_JOB_COST_USD": "1",
    "VIDEO_RECORDED_SPEND_USD": "10",
}


def test_worker_disabled_by_default():
    with patch.dict(
        "os.environ",
        {},
        clear=True,
    ):
        status = worker_readiness()

    assert status["ready_for_live_execution"] is False


def test_missing_configuration_blocks_start():
    with patch.dict(
        "os.environ",
        {},
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_explicit_configuration_passes():
    with patch.dict(
        "os.environ",
        READY_ENV,
        clear=True,
    ):
        status = require_worker_readiness()

    assert status["ready_for_live_execution"] is True
    assert status["durable_cost_enforcement"] is False


def test_missing_fal_key_blocks_start():
    config = {
        **READY_ENV,
        "FAL_KEY": "",
    }

    with patch.dict(
        "os.environ",
        config,
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_missing_billing_acknowledgment_blocks_start():
    config = {
        **READY_ENV,
        "ACKNOWLEDGE_PROVIDER_BILLING_RISK": "",
    }

    with patch.dict(
        "os.environ",
        config,
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_budget_over_limit_blocks_start():
    config = {
        **READY_ENV,
        "VIDEO_RECORDED_SPEND_USD": "50",
    }

    with patch.dict(
        "os.environ",
        config,
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_invalid_fal_model_blocks_start():
    config = {
        **READY_ENV,
        "FAL_MODEL_ID": "https://invalid.example/model",
    }

    with patch.dict(
        "os.environ",
        config,
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_wrong_provider_blocks_start():
    config = {
        **READY_ENV,
        "VIDEO_RENDER_PROVIDER": "disabled",
    }

    with patch.dict(
        "os.environ",
        config,
        clear=True,
    ):
        with pytest.raises(WorkerNotReadyError):
            require_worker_readiness()


def test_ready_status_does_not_expose_key():
    with patch.dict(
        "os.environ",
        READY_ENV,
        clear=True,
    ):
        result = worker_readiness()

    assert READY_ENV["FAL_KEY"] not in str(result)


def test_environment_toggle():
    with patch.dict(
        "os.environ",
        {"TEST_WORKER_FLAG": "YES"},
    ):
        assert env_enabled("TEST_WORKER_FLAG") is True

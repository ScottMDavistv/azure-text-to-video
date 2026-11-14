"""
Fail-closed configuration checks for the video worker.

No cloud connections or paid provider requests are made.

Budget checks use operator-provided estimates and are
NOT authoritative spending controls.
"""

import os

from fal_provider import validate_model_id
from provider_config import get_provider_name
from provider_credentials import credential_is_configured
from render_budget import (
    BudgetConfigurationError,
    BudgetLimitError,
    check_budget_preflight,
)


class WorkerNotReadyError(RuntimeError):
    pass


def env_enabled(name):
    return os.getenv(
        name, ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def worker_readiness():
    provider = get_provider_name()

    model_id = os.getenv(
        "FAL_MODEL_ID", ""
    ).strip()

    valid_model = False

    if model_id:
        try:
            validate_model_id(model_id)
            valid_model = True
        except ValueError:
            pass

    checks = {
        "worker_consumption_enabled": env_enabled(
            "ENABLE_WORKER_CONSUMPTION"
        ),
        "live_rendering_enabled": env_enabled(
            "ENABLE_LIVE_VIDEO_RENDERING"
        ),
        "billing_risk_acknowledged": env_enabled(
            "ACKNOWLEDGE_PROVIDER_BILLING_RISK"
        ),
        "fal_provider_selected": provider == "fal",
        "fal_model_configured": valid_model,
        "fal_credential_present": (
            credential_is_configured("fal")
        ),
        "azure_storage_account_configured": bool(
            os.getenv(
                "AZURE_STORAGE_ACCOUNT", ""
            ).strip()
        ),
        "azure_jobs_queue_configured": bool(
            os.getenv(
                "AZURE_JOBS_QUEUE", ""
            ).strip()
        ),
    }

    budget_status = "not_configured"

    try:
        check_budget_preflight()
        budget_status = "estimated_preflight_passed"
        checks["budget_estimate_passed"] = True
    except (
        BudgetConfigurationError,
        BudgetLimitError,
    ):
        checks["budget_estimate_passed"] = False

    ready = all(checks.values())

    return {
        "service": "textvideo-worker",
        "ready_for_live_execution": ready,
        "checks": checks,
        "budget_status": budget_status,
        "durable_cost_enforcement": False,
        "provider_access_verified": False,
        "azure_connectivity_verified": False,
    }


def require_worker_readiness():
    status = worker_readiness()

    if not status["ready_for_live_execution"]:
        missing = sorted(
            key
            for key, passed in status["checks"].items()
            if not passed
        )

        raise WorkerNotReadyError(
            "Worker startup blocked by configuration: "
            + ", ".join(missing)
        )

    return status

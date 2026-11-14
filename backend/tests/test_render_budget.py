from decimal import Decimal
from unittest.mock import patch

import pytest

from render_budget import (
    BudgetConfigurationError,
    BudgetEstimate,
    BudgetLimitError,
    check_budget_preflight,
    load_budget_estimate,
)


CONFIG = {
    "VIDEO_PER_JOB_LIMIT_USD": "2.00",
    "VIDEO_MONTHLY_LIMIT_USD": "50.00",
    "VIDEO_ESTIMATED_JOB_COST_USD": "1.25",
    "VIDEO_RECORDED_SPEND_USD": "10.00",
}


def test_valid_budget_preflight():
    with patch.dict("os.environ", CONFIG):
        result = check_budget_preflight()

    assert result["passed"] is True
    assert result["enforced_durable_spend_limit"] is False


def test_budget_uses_decimal():
    with patch.dict("os.environ", CONFIG):
        estimate = load_budget_estimate()

    assert estimate.monthly_limit == Decimal("50.00")


def test_per_job_limit():
    config = {
        **CONFIG,
        "VIDEO_ESTIMATED_JOB_COST_USD": "3",
    }

    with patch.dict("os.environ", config):
        with pytest.raises(BudgetLimitError):
            check_budget_preflight()


def test_monthly_limit():
    config = {
        **CONFIG,
        "VIDEO_RECORDED_SPEND_USD": "49.50",
    }

    with patch.dict("os.environ", config):
        with pytest.raises(BudgetLimitError):
            check_budget_preflight()


def test_exact_budget_allowed():
    estimate = BudgetEstimate(
        per_job_limit=Decimal("2"),
        monthly_limit=Decimal("50"),
        estimated_job_cost=Decimal("1"),
        recorded_monthly_spend=Decimal("49"),
    )

    assert estimate.check() is True


def test_missing_configuration():
    with patch.dict(
        "os.environ",
        {
            **CONFIG,
            "VIDEO_MONTHLY_LIMIT_USD": "",
        },
    ):
        with pytest.raises(BudgetConfigurationError):
            load_budget_estimate()


def test_negative_cost_rejected():
    config = {
        **CONFIG,
        "VIDEO_ESTIMATED_JOB_COST_USD": "-1",
    }

    with patch.dict("os.environ", config):
        with pytest.raises(BudgetConfigurationError):
            load_budget_estimate()


def test_nan_rejected():
    config = {
        **CONFIG,
        "VIDEO_ESTIMATED_JOB_COST_USD": "NaN",
    }

    with patch.dict("os.environ", config):
        with pytest.raises(BudgetConfigurationError):
            load_budget_estimate()


def test_infinity_rejected():
    config = {
        **CONFIG,
        "VIDEO_MONTHLY_LIMIT_USD": "Infinity",
    }

    with patch.dict("os.environ", config):
        with pytest.raises(BudgetConfigurationError):
            load_budget_estimate()


def test_zero_recorded_spend_allowed():
    config = {
        **CONFIG,
        "VIDEO_RECORDED_SPEND_USD": "0",
    }

    with patch.dict("os.environ", config):
        assert load_budget_estimate().check()

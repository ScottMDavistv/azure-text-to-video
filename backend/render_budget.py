"""
Provider-independent cost preflight.

This is a configuration check, NOT an authoritative
spending ledger and NOT a guaranteed monthly spending cap.

Live charges must eventually be controlled with durable
per-request reservations and provider billing reconciliation.
"""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class BudgetConfigurationError(ValueError):
    pass


class BudgetLimitError(RuntimeError):
    pass


def positive_money(value, field_name):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetConfigurationError(
            f"Invalid {field_name}"
        ) from None

    if not amount.is_finite() or amount <= 0:
        raise BudgetConfigurationError(
            f"{field_name} must be positive"
        )

    return amount


def nonnegative_money(value, field_name):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetConfigurationError(
            f"Invalid {field_name}"
        ) from None

    if not amount.is_finite() or amount < 0:
        raise BudgetConfigurationError(
            f"{field_name} cannot be negative"
        )

    return amount


@dataclass(frozen=True)
class BudgetEstimate:
    per_job_limit: Decimal
    monthly_limit: Decimal
    estimated_job_cost: Decimal
    recorded_monthly_spend: Decimal

    def check(self):
        if self.estimated_job_cost > self.per_job_limit:
            raise BudgetLimitError(
                "Estimated job cost exceeds per-job limit"
            )

        if (
            self.recorded_monthly_spend
            + self.estimated_job_cost
            > self.monthly_limit
        ):
            raise BudgetLimitError(
                "Estimated monthly budget exceeded"
            )

        return True


def load_budget_estimate():
    required = {
        "per_job_limit": "VIDEO_PER_JOB_LIMIT_USD",
        "monthly_limit": "VIDEO_MONTHLY_LIMIT_USD",
        "estimated_job_cost": "VIDEO_ESTIMATED_JOB_COST_USD",
        "recorded_monthly_spend": "VIDEO_RECORDED_SPEND_USD",
    }

    values = {}

    for field_name, env_name in required.items():
        raw = os.getenv(env_name)

        if raw is None or not raw.strip():
            raise BudgetConfigurationError(
                f"Missing {env_name}"
            )

        if field_name == "recorded_monthly_spend":
            values[field_name] = nonnegative_money(
                raw,
                env_name,
            )
        else:
            values[field_name] = positive_money(
                raw,
                env_name,
            )

    return BudgetEstimate(**values)


def check_budget_preflight():
    estimate = load_budget_estimate()
    estimate.check()

    return {
        "passed": True,
        "accounting": "operator-supplied-estimates",
        "enforced_durable_spend_limit": False,
    }

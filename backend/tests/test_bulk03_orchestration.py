from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from fal_budget_guard import verify_job_reservation, has_capacity
from held_jobs import (
    HeldJobDenied, hold_job, cancel_held_job, reconsider_held_job,
)
from job_scheduler import choose_next


def uid():
    return str(uuid4())


def job(project, *, priority="normal", minutes=0, reason="project_budget"):
    return {
        "jobId": uid(), "projectId": project, "status": "held",
        "holdReason": reason, "priority": priority,
        "heldAt": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
    }


def test_high_priority_preferred():
    a, b = uid(), uid()
    normal = job(a, minutes=3)
    high = job(b, priority="high")
    assert choose_next([normal, high])["jobId"] == high["jobId"]


def test_old_normal_is_promoted():
    older, newer = job(uid(), minutes=150), job(uid(), priority="high")
    assert choose_next([newer, older], starvation_minutes=120)["jobId"] == older["jobId"]


def test_project_fairness():
    a, b = uid(), uid()
    first, second = job(a), job(b)
    assert choose_next([first, second], last_project_id=a)["projectId"] == b


def test_non_held_jobs_not_selected():
    item = job(uid())
    item["status"] = "cancelled"
    assert choose_next([item]) is None


def test_hold_creates_durable_record():
    table = MagicMock()
    result = hold_job(table, uid(), uid(), 100,
                      reason="project_budget", owner_id=uid())
    assert result["status"] == "held"
    table.create_entity.assert_called_once()


def test_hold_rejects_duplicate():
    from azure.core.exceptions import ResourceExistsError
    table = MagicMock()
    table.create_entity.side_effect = ResourceExistsError("duplicate")
    with pytest.raises(HeldJobDenied):
        hold_job(table, uid(), uid(), 100,
                 reason="global_fal_limit", owner_id=uid())


def test_cancel_requires_authorization():
    table = MagicMock()
    table.get_entity.return_value = {"status": "held", "projectId": uid()}
    with pytest.raises(HeldJobDenied):
        cancel_held_job(table, uid(), actor_id=uid(),
                        authorize_cancellation=lambda *args: False)
    table.update_entity.assert_not_called()


def test_reconsider_still_held_without_capacity():
    table = MagicMock()
    table.get_entity.return_value = {
        "status": "held", "projectId": uid(), "estimatedCents": 100,
    }
    assert reconsider_held_job(table, uid(), verify_budget=lambda *args: False) == "held"
    table.update_entity.assert_not_called()


def test_reservation_rejects_other_project():
    table = MagicMock()
    project = uid()
    with patch("fal_budget_guard.load", return_value={
        "state": "reserved", "projectId": uid(), "reservedCents": 10,
    }):
        assert verify_job_reservation(table, uid(), project) is False


def test_reservation_requires_spending_capacity_records():
    project = uid()
    rows = [
        {"state": "reserved", "projectId": project, "reservedCents": 10},
        {"ceilingCents": 100, "spentCents": 0, "reservedCents": 10},
        {"ceilingCents": 100, "spentCents": 0, "reservedCents": 10},
    ]
    with patch("fal_budget_guard.load", side_effect=rows):
        assert verify_job_reservation(MagicMock(), uid(), project) is True


def test_advisory_capacity_rejects_insufficient_balance():
    with patch("fal_budget_guard.load", return_value={
        "ceilingCents": 100, "spentCents": 50, "reservedCents": 50,
    }):
        assert has_capacity(MagicMock(), uid(), 1) is False

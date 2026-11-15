from unittest.mock import MagicMock, patch
from uuid import uuid4

from held_job_coordinator import (
    dispatch_next_project_job, reconsider_after_budget_approval,
)


def uid():
    return str(uuid4())


def test_no_capacity_no_claim():
    with (
        patch("held_job_coordinator.list_project_holds", return_value=[{
            "jobId": uid(), "projectId": uid(), "estimatedCents": 100,
            "status": "held",
        }]),
        patch("held_job_coordinator.has_capacity", return_value=False),
        patch("held_job_coordinator.dispatch_candidate") as dispatch,
    ):
        result = dispatch_next_project_job(MagicMock(), MagicMock(), uid())
    assert result["status"] == "no_eligible_jobs"
    dispatch.assert_not_called()


def test_eligible_job_claimed_and_reserved():
    project, job_id = uid(), uid()
    record = {
        "jobId": job_id, "projectId": project,
        "estimatedCents": 100, "status": "held",
        "holdReason": "project_budget", "priority": "high",
        "heldAt": "2026-11-15T12:00:00+00:00",
    }
    with (
        patch("held_job_coordinator.list_project_holds", return_value=[record]),
        patch("held_job_coordinator.has_capacity", return_value=True),
        patch("held_job_coordinator.dispatch_candidate",
              return_value={"reservedCents": 100}) as dispatch,
    ):
        result = dispatch_next_project_job(MagicMock(), MagicMock(), project)
    assert result["status"] == "reserved"
    assert result["job_id"] == job_id
    dispatch.assert_called_once()


def test_budget_approval_invokes_batch_reconsideration():
    project = uid()
    with patch("held_job_coordinator.reconsider_project_holds",
               return_value={"eligible": 1}) as reconsider:
        result = reconsider_after_budget_approval(
            MagicMock(), MagicMock(), project
        )
    assert result == {"eligible": 1}
    reconsider.assert_called_once()

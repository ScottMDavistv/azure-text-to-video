from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from budgeted_fal_execution import (
    ExecutionBlocked, run_reserved_fal_job, settle_verified_charge,
)


def uid():
    return str(uuid4())


def options(enabled=True):
    job_id, project_id = uid(), uid()
    jobs = MagicMock()
    jobs.get_entity.return_value = {
        "projectId": project_id, "status": "processing", "prompt": "Ocean sunrise",
    }
    return {
        "enabled": enabled, "job_id": job_id,
        "jobs_table": jobs, "finance_table": MagicMock(),
        "submission_table": MagicMock(), "client": MagicMock(),
        "authorize_job": lambda *args: True,
    }


def test_disabled_no_storage_or_provider():
    args = options(enabled=False)
    with pytest.raises(ExecutionBlocked):
        run_reserved_fal_job(**args)
    args["client"].submit.assert_not_called()
    args["jobs_table"].get_entity.assert_not_called()


def test_unauthorized_job_no_submit():
    args = options()
    args["authorize_job"] = lambda *a: False
    with pytest.raises(ExecutionBlocked):
        run_reserved_fal_job(**args)
    args["client"].submit.assert_not_called()


def test_unclaimed_job_no_submit():
    args = options()
    args["jobs_table"].get_entity.return_value["status"] = "queued"
    with pytest.raises(ExecutionBlocked):
        run_reserved_fal_job(**args)
    args["client"].submit.assert_not_called()


def test_missing_project_fails_closed():
    args = options()
    del args["jobs_table"].get_entity.return_value["projectId"]
    with pytest.raises(Exception):
        run_reserved_fal_job(**args)
    args["client"].submit.assert_not_called()


def test_success_never_settles_unverified_cost():
    args = options()
    with (
        patch("budgeted_fal_execution.make_reservation_verifier",
              return_value=lambda job: True),
        patch("budgeted_fal_execution.submit_and_wait_guarded",
              return_value={"video_url": "https://fal.media/out.mp4"}) as submit,
    ):
        result = run_reserved_fal_job(**args)
    assert result["billing_status"] == "awaiting_verified_charge"
    assert result["reservation_state"] == "reserved"
    submit.assert_called_once()


def test_settlement_needs_verified_billing():
    finance = MagicMock()
    with pytest.raises(ExecutionBlocked):
        settle_verified_charge(
            finance_table=finance, job_id=uid(),
            charge_cents=100, verified_billing=lambda *args: False,
        )
    finance.submit_transaction.assert_not_called()


def test_settlement_delegates_verified_charge():
    finance = MagicMock()
    with patch("finance_controls.settle", return_value={"state": "settled"}) as settle:
        result = settle_verified_charge(
            finance_table=finance, job_id=uid(), charge_cents=100,
            verified_billing=lambda *args: True,
        )
    assert result["state"] == "settled"
    settle.assert_called_once()

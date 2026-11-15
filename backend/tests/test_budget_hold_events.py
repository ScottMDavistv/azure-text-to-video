from unittest.mock import MagicMock, patch
from uuid import uuid4

from budget_hold_events import (
    emit_hold_notice, on_budget_approval, reconcile_hold_notifications,
)


def uid():
    return str(uuid4())


def test_hold_notice_is_deduplicated_by_job_id():
    hold = {
        "projectId": uid(), "ownerId": uid(), "jobId": uid(),
        "holdReason": "project_budget", "estimatedCents": 250,
    }
    with patch("budget_hold_events.queue_notification") as queue:
        emit_hold_notice(MagicMock(), hold)
    assert queue.call_args.kwargs["idempotency_key"] == "hold:" + hold["jobId"]


def test_approval_calls_reconsideration_and_notifier():
    with (
        patch("budget_hold_events.reconsider_after_budget_approval",
              return_value={"eligible": 2}) as rescan,
        patch("budget_hold_events.emit_approval_notice") as notify,
    ):
        result = on_budget_approval(
            MagicMock(), MagicMock(), MagicMock(),
            request_id=uid(), project_id=uid(), owner_id=uid(),
        )
    assert result == {"eligible": 2}
    notify.assert_called_once()
    rescan.assert_called_once()


def test_reconcile_held_job_notifications():
    row = {
        "projectId": uid(), "ownerId": uid(), "jobId": uid(),
        "holdReason": "global_fal_limit", "estimatedCents": 80,
    }
    with (
        patch("budget_hold_events.list_project_holds",
              return_value=[row]),
        patch("budget_hold_events.emit_hold_notice") as notify,
    ):
        result = reconcile_hold_notifications(
            MagicMock(), MagicMock(), row["projectId"],
        )
    assert result == {"new_notifications": 1}
    notify.assert_called_once()

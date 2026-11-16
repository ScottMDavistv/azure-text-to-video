from unittest.mock import MagicMock, patch
from uuid import uuid4
import json
import pytest

from reconciliation_runner import (
    ReconciliationDisabled, run_project_reconciliation,
    queue_delivery_sink, flush_notifications,
)


def uid():
    return str(uuid4())


def test_disabled_scan_never_reads_tables():
    held = MagicMock()
    with pytest.raises(ReconciliationDisabled):
        run_project_reconciliation(
            enabled=False, project_ids=[uid()], held_table=held,
            finance_table=MagicMock(), outbox_table=MagicMock(),
        )
    held.query_entities.assert_not_called()


def test_project_scan_reconsiders_and_repairs_notifications():
    project = uid()
    with (
        patch("reconciliation_runner.reconsider_after_budget_approval",
              return_value={"held": 1}) as reconsider,
        patch("reconciliation_runner.reconcile_hold_notifications",
              return_value={"new_notifications": 1}) as notices,
    ):
        result = run_project_reconciliation(
            enabled=True, project_ids=[project],
            held_table=MagicMock(), finance_table=MagicMock(),
            outbox_table=MagicMock(),
        )
    assert result["projects_scanned"] == 1
    reconsider.assert_called_once()
    notices.assert_called_once()


def test_queue_transport_uses_stable_event_id_without_secrets():
    queue = MagicMock()
    sink = queue_delivery_sink(queue)
    sink("evt-123", {
        "eventType": "job_held", "projectId": uid(),
        "targetUserId": uid(), "jobId": uid(),
        "prompt": "PRIVATE", "apiKey": "SECRET",
    })
    message = json.loads(queue.send_message.call_args.args[0])
    assert message["event_id"] == "evt-123"
    assert "prompt" not in message
    assert "apiKey" not in message


def test_notification_flush_disabled():
    queue = MagicMock()
    with pytest.raises(ReconciliationDisabled):
        flush_notifications(
            enabled=False, outbox_table=MagicMock(), queue_client=queue
        )
    queue.send_message.assert_not_called()

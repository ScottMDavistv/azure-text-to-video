"""No paid calls. Test fail-closed boundaries and coordination hooks."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from project_access import Actor, Action, AccessDenied
from project_models import Project, ProjectRole
from project_security import resolve_verified_actor, require_project_role
from financial_outbox import (
    queue_notification, deliver_notification, OutboxError,
)
from safe_reconciliation import run_notification_delivery
from budgeted_worker_service import (
    PaidWorkerDisabled, process_reserved_job, run_budgeted_once,
)


def uid():
    return str(uuid4())


class Record(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.metadata = {"etag": 'W/"etag"'}


def test_verified_actor_requires_trusted_verifier():
    with pytest.raises(AccessDenied):
        resolve_verified_actor("token", verify_principal=None)
    with pytest.raises(AccessDenied):
        resolve_verified_actor("token", verify_principal=lambda _: {
            "verified": False, "user_id": uid(),
        })


def test_verified_actor_uses_server_result():
    user = uid()
    actor = resolve_verified_actor("token", verify_principal=lambda _: {
        "verified": True, "user_id": user,
    })
    assert actor.user_id == user and actor.authenticated


def test_membership_is_not_client_role():
    project = uid()
    actor = Actor(uid(), True)
    with (
        patch("project_security.get_project",
              return_value=Project(project, "Test", actor.user_id)),
        patch("project_security.get_membership_role",
              return_value=ProjectRole.MEMBER),
    ):
        with pytest.raises(AccessDenied):
            require_project_role(MagicMock(), actor, project, Action.REQUEST_BUDGET)


def test_owner_membership_allows_budget_request():
    project = uid()
    actor = Actor(uid(), True)
    with (
        patch("project_security.get_project",
              return_value=Project(project, "Test", actor.user_id)),
        patch("project_security.get_membership_role",
              return_value=ProjectRole.OWNER),
    ):
        assert require_project_role(
            MagicMock(), actor, project, Action.REQUEST_BUDGET,
        ) == ProjectRole.OWNER


def test_outbox_persist_before_delivery():
    table = MagicMock()
    event = queue_notification(
        table, event_type="job_held", project_id=uid(),
        target_user_id=uid(), job_id=uid(),
        payload={"reason": "project_budget"},
    )
    assert event["status"] == "pending"
    table.create_entity.assert_called_once()


def test_outbox_requires_real_delivery_sink():
    table = MagicMock()
    table.get_entity.return_value = Record(
        status="pending", attempts=0, RowKey="test",
        PartitionKey="render-notification-outbox",
    )
    with pytest.raises(OutboxError):
        deliver_notification(table, "test", deliver=None)
    table.update_entity.assert_not_called()


def test_outbox_uses_etag_claim():
    table = MagicMock()
    table.get_entity.side_effect = [
        Record(status="pending", attempts=0, RowKey="test",
               PartitionKey="render-notification-outbox"),
        Record(status="delivering", attempts=1, RowKey="test",
               PartitionKey="render-notification-outbox"),
    ]
    sink = MagicMock()
    assert deliver_notification(table, "test", deliver=sink) == "delivered"
    assert sink.call_args.args[0] == "test"
    assert table.update_entity.call_count == 2
    assert table.update_entity.call_args_list[0].kwargs["etag"] == 'W/"etag"'


def test_notification_delivery_scan():
    table = MagicMock()
    with (
        patch("safe_reconciliation.pending_notifications",
              return_value=[{"RowKey": "x"}]),
        patch("safe_reconciliation.deliver_notification") as deliver,
    ):
        result = run_notification_delivery(table, deliver=MagicMock())
    assert result == {"delivered": 1, "scanned": 1}
    deliver.assert_called_once()


def test_paid_worker_disabled_no_provider(monkeypatch):
    monkeypatch.delenv("ENABLE_PAID_FAL_WORKER", raising=False)
    client = MagicMock()
    with pytest.raises(PaidWorkerDisabled):
        process_reserved_job(
            uid(), enabled=True, jobs_table=MagicMock(),
            finance_table=MagicMock(), submission_table=MagicMock(),
            client=client, authorize_job=lambda *a: True,
        )
    client.submit.assert_not_called()


def test_budgeted_queue_disabled_does_not_read_queue(monkeypatch):
    monkeypatch.delenv("ENABLE_PAID_FAL_WORKER", raising=False)
    queue = MagicMock()
    with pytest.raises(PaidWorkerDisabled):
        run_budgeted_once(
            enabled=True, queue_client=queue, jobs_table=MagicMock(),
            finance_table=MagicMock(), submission_table=MagicMock(),
            client=MagicMock(), authorize_job=lambda *a: True,
        )
    queue.receive_messages.assert_not_called()


def test_authorization_blocks_before_claim(monkeypatch):
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    client = MagicMock()
    project = uid()
    jobs = MagicMock()
    jobs.get_entity.return_value = {
        "projectId": project, "status": "queued",
    }
    with patch("budgeted_worker_service._claim_on_table") as claim:
        with pytest.raises(PaidWorkerDisabled):
            process_reserved_job(
                uid(), enabled=True, jobs_table=jobs,
                finance_table=MagicMock(), submission_table=MagicMock(),
                client=client, authorize_job=lambda *a: False,
            )
    claim.assert_not_called()
    client.submit.assert_not_called()


def test_financial_denial_blocks_claim(monkeypatch):
    monkeypatch.setenv("ENABLE_PAID_FAL_WORKER", "true")
    jobs = MagicMock()
    jobs.get_entity.return_value = {
        "projectId": uid(), "status": "queued",
    }
    with (
        patch("budgeted_worker_service.verify_job_reservation",
              return_value=False),
        patch("budgeted_worker_service._claim_on_table") as claim,
    ):
        with pytest.raises(PaidWorkerDisabled):
            process_reserved_job(
                uid(), enabled=True, jobs_table=jobs,
                finance_table=MagicMock(), submission_table=MagicMock(),
                client=MagicMock(), authorize_job=lambda *a: True,
            )
    claim.assert_not_called()

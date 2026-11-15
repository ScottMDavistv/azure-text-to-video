from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceExistsError

from notification_inbox import apply_notification_once, InboxBlocked
from distributed_fairness import (
    reserve_scheduler_slot, finish_scheduler_slot, FairnessBusy
)
from stage_a_dispatch import dispatch_fair_once, StageADispatchBlocked
from stage_a_coordinator import run_stage_a_once, StageABlocked
from stage_a_runtime import validate_safe_defaults


def uid():
    return str(uuid4())


class Row(dict):
    def __init__(self, **kw):
        super().__init__(kw)
        self.metadata = {"etag": 'W/"current"'}


def test_nonbillable_runtime_rejects_paid_worker():
    with pytest.raises(RuntimeError):
        validate_safe_defaults({"ENABLE_PAID_FAL_WORKER": "true"})
    with pytest.raises(RuntimeError):
        validate_safe_defaults({"FAL_KEY": "test"})


def test_coordinator_rejects_untrusted_project_list_without_storage():
    held = MagicMock()
    with pytest.raises(Exception):
        run_stage_a_once(
            enabled=True, project_ids=["untrusted"], held_table=held,
            finance_table=MagicMock(), outbox_table=MagicMock(),
        )
    held.query_entities.assert_not_called()


def test_confirmed_hold_repair_is_wired_into_scan():
    project, job_id = uid(), uid()
    with (
        patch("stage_a_coordinator.list_project_holds", return_value=[{
            "jobId": job_id, "status": "reserving"
        }]),
        patch("stage_a_coordinator.reconcile_confirmed_reservation_atomic",
              return_value="reserved") as repair,
        patch("stage_a_coordinator.run_project_reconciliation",
              return_value={"projects_scanned": 1}),
    ):
        result = run_stage_a_once(
            enabled=True, project_ids=[project], held_table=MagicMock(),
            finance_table=MagicMock(), outbox_table=MagicMock(),
        )
    assert result["reservations_repaired"] == 1
    repair.assert_called_once()


def test_notification_inbox_applies_only_once():
    table = MagicMock()
    table.get_entity.return_value = Row(status="processing")
    effect = MagicMock()
    assert apply_notification_once(table, "event:1", {}, apply=effect) == "applied"
    effect.assert_called_once()
    table.update_entity.assert_called_once()


def test_notification_duplicate_is_ignored_without_second_effect():
    table = MagicMock()
    table.create_entity.side_effect = ResourceExistsError("exists")
    table.get_entity.return_value = Row(status="applied")
    effect = MagicMock()
    assert apply_notification_once(table, "event:1", {}, apply=effect) == "duplicate_ignored"
    effect.assert_not_called()


def test_notification_ambiguous_prior_claim_fails_closed():
    table = MagicMock()
    table.create_entity.side_effect = ResourceExistsError("exists")
    table.get_entity.return_value = Row(status="processing")
    with pytest.raises(InboxBlocked):
        apply_notification_once(table, "event:1", {}, apply=MagicMock())


def test_fairness_lease_excludes_concurrent_workers():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    table.get_entity.return_value = Row(
        ownerId=uid(), lastProjectId="", leaseUntil=(now + timedelta(seconds=20)).isoformat()
    )
    with pytest.raises(FairnessBusy):
        reserve_scheduler_slot(table, uid(), now_time=now)
    table.update_entity.assert_not_called()


def test_fairness_lease_claim_and_release_remembers_project():
    table = MagicMock()
    owner, project = uid(), uid()
    now = datetime.now(timezone.utc)
    table.get_entity.side_effect = [
        Row(ownerId="", lastProjectId="",
            leaseUntil=(now - timedelta(seconds=1)).isoformat()),
        Row(ownerId=owner, lastProjectId="",
            leaseUntil=(now + timedelta(seconds=20)).isoformat()),
    ]
    reserve_scheduler_slot(table, owner, now_time=now)
    assert finish_scheduler_slot(table, owner, project, now_time=now) == "released"
    assert table.update_entity.call_args.kwargs["entity"]["lastProjectId"] == project


def test_dispatch_disabled_before_financial_read():
    held = MagicMock()
    with pytest.raises(StageADispatchBlocked):
        dispatch_fair_once(
            enabled=False, scheduler_table=MagicMock(), held_table=held,
            finance_table=MagicMock(), project_ids=[uid()], scheduler_id=uid(),
        )
    held.query_entities.assert_not_called()


def test_dispatch_advances_project_only_after_successful_reservation():
    owner, project, job = uid(), uid(), uid()
    with (
        patch("stage_a_dispatch.reserve_scheduler_slot",
              return_value={"lastProjectId": ""}),
        patch("stage_a_dispatch.list_project_holds", return_value=[{
            "status": "eligible", "jobId": job, "projectId": project,
            "estimatedCents": 50, "holdReason": "project_budget",
            "priority": "high", "heldAt": datetime.now(timezone.utc).isoformat(),
        }]),
        patch("stage_a_dispatch.has_capacity", return_value=True),
        patch("stage_a_dispatch.dispatch_candidate",
              return_value={"state": "reserved"}) as reserve,
        patch("stage_a_dispatch.finish_scheduler_slot") as finish,
    ):
        result = dispatch_fair_once(
            enabled=True, scheduler_table=MagicMock(), held_table=MagicMock(),
            finance_table=MagicMock(), project_ids=[project], scheduler_id=owner,
        )
    assert result["status"] == "reserved"
    reserve.assert_called_once()
    finish.assert_called_once()


def test_dispatch_not_advancing_project_when_reservation_ambiguous():
    owner, project, job = uid(), uid(), uid()
    with (
        patch("stage_a_dispatch.reserve_scheduler_slot",
              return_value={"lastProjectId": ""}),
        patch("stage_a_dispatch.list_project_holds", return_value=[{
            "status": "eligible", "jobId": job, "projectId": project,
            "estimatedCents": 50, "holdReason": "project_budget",
            "priority": "high", "heldAt": datetime.now(timezone.utc).isoformat(),
        }]),
        patch("stage_a_dispatch.has_capacity", return_value=True),
        patch("stage_a_dispatch.dispatch_candidate",
              side_effect=RuntimeError("ambiguous")),
        patch("stage_a_dispatch.finish_scheduler_slot") as finish,
    ):
        with pytest.raises(RuntimeError):
            dispatch_fair_once(
                enabled=True, scheduler_table=MagicMock(), held_table=MagicMock(),
                finance_table=MagicMock(), project_ids=[project], scheduler_id=owner,
            )
    finish.assert_not_called()

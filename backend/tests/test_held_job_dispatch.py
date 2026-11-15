from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from azure.core.exceptions import HttpResponseError

from held_job_dispatch import (
    claim_for_reservation, dispatch_candidate,
    reconcile_reserving, reserve_claimed_job,
)
from held_jobs import HeldJobDenied


def uid():
    return str(uuid4())


class Record(dict):
    def __init__(self, **data):
        super().__init__(data)
        self.metadata = {"etag": 'W/"test"'}


def test_claim_is_conditional():
    table = MagicMock()
    job_id = uid()
    table.get_entity.return_value = Record(
        status="held", RowKey=job_id, PartitionKey="fal-held-jobs",
    )
    result = claim_for_reservation(table, job_id)
    assert result["status"] == "reserving"
    kwargs = table.update_entity.call_args.kwargs
    assert kwargs["etag"] == 'W/"test"'


def test_concurrent_claim_denied():
    table = MagicMock()
    table.get_entity.return_value = Record(
        status="held", RowKey=uid(), PartitionKey="fal-held-jobs",
    )
    table.update_entity.side_effect = HttpResponseError("ETag conflict")
    with pytest.raises(HeldJobDenied):
        claim_for_reservation(table, uid())


def test_cannot_claim_twice():
    table = MagicMock()
    table.get_entity.return_value = Record(status="reserving")
    with pytest.raises(HeldJobDenied):
        claim_for_reservation(table, uid())
    table.update_entity.assert_not_called()


def test_reserve_before_dispatch_state():
    table = MagicMock()
    finance = MagicMock()
    project_id = uid()
    job_id = uid()
    table.get_entity.return_value = Record(
        status="reserving", projectId=project_id,
        estimatedCents=100,
    )
    with patch("held_job_dispatch.reserve", return_value={"state": "reserved"}) as save:
        assert reserve_claimed_job(table, finance, job_id) == {"state": "reserved"}
    save.assert_called_once_with(finance, project_id, job_id, 100)
    assert table.update_entity.call_args.kwargs["entity"]["status"] == "reserved"


def test_finance_failure_does_not_mark_reserved():
    table = MagicMock()
    finance = MagicMock()
    table.get_entity.return_value = Record(
        status="reserving", projectId=uid(), estimatedCents=100
    )
    with patch("held_job_dispatch.reserve", side_effect=RuntimeError("uncertain")):
        with pytest.raises(RuntimeError):
            reserve_claimed_job(table, finance, uid())
    table.update_entity.assert_not_called()


def test_dispatch_calls_claim_before_reserve():
    with (
        patch("held_job_dispatch.claim_for_reservation") as claim,
        patch("held_job_dispatch.reserve_claimed_job", return_value="reserved") as reserve,
    ):
        assert dispatch_candidate(MagicMock(), MagicMock(), uid()) == "reserved"
    claim.assert_called_once()
    reserve.assert_called_once()


def test_reconciliation_only_reads():
    held = MagicMock()
    finance = MagicMock()
    project = uid()
    held.get_entity.return_value = Record(
        status="reserving", projectId=project, estimatedCents=100
    )
    with patch("finance_controls.load", return_value={
        "state": "reserved", "projectId": project, "reservedCents": 100,
    }):
        result = reconcile_reserving(held, finance, uid())
    assert result == "manual_review_reservation_exists"
    finance.submit_transaction.assert_not_called()
    held.update_entity.assert_not_called()


def test_reconcile_only_confirmed_financial_reservation():
    from held_job_dispatch import reconcile_confirmed_reservation
    project, jid = uid(), uid()
    held, finance = MagicMock(), MagicMock()
    held.get_entity.return_value = Record(
        PartitionKey="fal-held-jobs", RowKey=jid,
        status="reserving", projectId=project, estimatedCents=100,
    )
    audit = MagicMock(return_value=True)
    with patch("finance_controls.load", return_value={
        "projectId": project, "state": "reserved", "reservedCents": 100,
    }):
        result = reconcile_confirmed_reservation(
            held, finance, jid, record_audit=audit,
        )
    assert result == "reserved"
    assert audit.call_args.args[0] == "reservation_reconciled:" + jid
    assert held.update_entity.call_args.kwargs["entity"]["status"] == "reserved"
    finance.submit_transaction.assert_not_called()


def test_reconcile_inconsistent_finance_stays_blocked():
    from held_job_dispatch import reconcile_confirmed_reservation
    project, jid = uid(), uid()
    held, finance = MagicMock(), MagicMock()
    held.get_entity.return_value = Record(
        status="reserving", projectId=project, estimatedCents=100,
    )
    with patch("finance_controls.load", return_value={
        "projectId": uid(), "state": "reserved", "reservedCents": 100,
    }):
        with pytest.raises(HeldJobDenied):
            reconcile_confirmed_reservation(
                held, finance, jid, record_audit=lambda *a: True,
            )
    held.update_entity.assert_not_called()
    finance.submit_transaction.assert_not_called()


def test_reconcile_requires_audit_before_state_change():
    from held_job_dispatch import reconcile_confirmed_reservation
    project, jid = uid(), uid()
    held, finance = MagicMock(), MagicMock()
    held.get_entity.return_value = Record(
        status="reserving", projectId=project, estimatedCents=100,
    )
    with patch("finance_controls.load", return_value={
        "projectId": project, "state": "reserved", "reservedCents": 100,
    }):
        with pytest.raises(HeldJobDenied, match="audit"):
            reconcile_confirmed_reservation(
                held, finance, jid, record_audit=lambda *a: False,
            )
    held.update_entity.assert_not_called()

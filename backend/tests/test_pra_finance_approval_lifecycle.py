"""Adversarial tests for PRA-06 budget approval lifecycle safeguards."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from finance_controls import FinanceDenied, approve_increase, cancel_increase
from project_access import Actor


def uid():
    return str(uuid4())


class Row(dict):
    def __init__(self, **fields):
        super().__init__(PartitionKey="render-finance-v1", **fields)
        self.metadata = {"etag": 'W/"test"'}


def pending(request_id, requester, first, *, created_at=None):
    return Row(
        RowKey="increase:" + request_id,
        requesterId=requester,
        firstApproverId=first,
        secondApproverId="",
        scope="global:fal",
        proposedCents=500,
        status="pending",
        createdAt=created_at if created_at is not None
        else datetime.now(timezone.utc).isoformat(),
    )


def test_revoked_first_admin_prevents_second_approval():
    rid, requester, first, second = uid(), uid(), uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = pending(rid, requester, first)
    # Current admin remains valid; first approver has been revoked.
    with pytest.raises(FinanceDenied, match="revoked"):
        approve_increase(
            table, Actor(second, True), rid,
            lambda person: person == second,
        )
    table.submit_transaction.assert_not_called()


@pytest.mark.parametrize("age", [timedelta(days=365), timedelta(days=2)])
def test_expired_increase_cannot_apply(age):
    rid, requester, first, second = uid(), uid(), uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = pending(
        rid, requester, first,
        created_at=(datetime.now(timezone.utc) - age).isoformat(),
    )
    with pytest.raises(FinanceDenied, match="expired"):
        approve_increase(table, Actor(second, True), rid, lambda _: True)
    table.submit_transaction.assert_not_called()


def test_missing_created_at_fails_closed():
    rid, requester, first, second = uid(), uid(), uid(), uid()
    table = MagicMock()
    row = pending(rid, requester, first)
    del row["createdAt"]
    table.get_entity.return_value = row
    with pytest.raises(FinanceDenied, match="invalid"):
        approve_increase(table, Actor(second, True), rid, lambda _: True)
    table.submit_transaction.assert_not_called()


def test_future_dated_request_fails_closed():
    rid, requester, first, second = uid(), uid(), uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = pending(
        rid, requester, first,
        created_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    with pytest.raises(FinanceDenied):
        approve_increase(table, Actor(second, True), rid, lambda _: True)
    table.submit_transaction.assert_not_called()


def test_requester_can_cancel_with_atomic_audit():
    rid, requester = uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = pending(rid, requester, "")
    assert cancel_increase(table, Actor(requester, True), rid) == "cancelled"
    actions = table.submit_transaction.call_args.args[0]
    assert len(actions) == 2
    assert actions[0][0] == "update"
    assert actions[0][1]["status"] == "cancelled"
    assert actions[0][1]["cancelledBy"] == requester
    assert actions[1][0] == "create"
    assert actions[1][1]["eventType"] == "increase_cancelled"


def test_nonrequester_cannot_cancel_without_verified_admin():
    rid, requester, other = uid(), uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = pending(rid, requester, "")
    with pytest.raises(FinanceDenied, match="administrator"):
        cancel_increase(table, Actor(other, True), rid,
                        admin_lookup=lambda _: False)
    table.submit_transaction.assert_not_called()


def test_cancelled_request_cannot_be_approved():
    rid, requester, first, second = uid(), uid(), uid(), uid()
    table = MagicMock()
    row = pending(rid, requester, first)
    row["status"] = "cancelled"
    table.get_entity.return_value = row
    with pytest.raises(FinanceDenied, match="not pending"):
        approve_increase(table, Actor(second, True), rid, lambda _: True)
    table.submit_transaction.assert_not_called()

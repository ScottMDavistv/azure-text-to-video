from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from project_access import Actor
from project_models import ProjectRole

from finance_controls import (
    FinanceDenied,
    approve_increase,
    check_money,
    provision_limit,
    request_increase,
    reserve,
    settle,
)


def uid():
    return str(uuid4())


class Record(dict):
    def __init__(self, **values):
        super().__init__(values)
        self.metadata = {"etag": 'W/"test"'}


def record(row, **fields):
    return Record(
        PartitionKey="render-finance-v1",
        RowKey=row,
        **fields,
    )


def test_money_rejects_float():
    with pytest.raises(FinanceDenied):
        check_money(1.25)


def test_money_rejects_negative():
    with pytest.raises(FinanceDenied):
        check_money(-1)


def test_money_rejects_bool():
    with pytest.raises(FinanceDenied):
        check_money(True)


def test_money_accepts_integer_cents():
    assert check_money(125) == 125


def test_admin_required_for_provision():
    table = MagicMock()
    actor = Actor(uid(), True)

    with pytest.raises(FinanceDenied):
        provision_limit(
            table, actor, "global:fal",
            5000, lambda user: False
        )

    table.submit_transaction.assert_not_called()


def test_provision_creates_limit_and_audit():
    table = MagicMock()
    actor = Actor(uid(), True)

    provision_limit(
        table, actor, "global:fal",
        5000, lambda user: True
    )

    actions = table.submit_transaction.call_args.args[0]
    assert len(actions) == 2
    assert actions[0][0] == "create"


def test_reservation_updates_both_limits():
    table = MagicMock()
    project_id = uid()
    job_id = uid()

    def fetch(partition_key, row_key):
        return record(
            row_key,
            ceilingCents=5000,
            spentCents=0,
            reservedCents=0,
        )

    table.get_entity.side_effect = fetch

    result = reserve(
        table, project_id, job_id, 150
    )

    assert result["reservedCents"] == 150

    actions = table.submit_transaction.call_args.args[0]
    assert len(actions) == 4
    assert actions[0][0] == "update"
    assert actions[1][0] == "update"
    assert actions[2][0] == "create"


def test_budget_exceeded_blocks_transaction():
    table = MagicMock()

    table.get_entity.return_value = record(
        "limit",
        ceilingCents=100,
        reservedCents=90,
        spentCents=0,
    )

    with pytest.raises(
        FinanceDenied,
        match="Insufficient",
    ):
        reserve(table, uid(), uid(), 20)

    table.submit_transaction.assert_not_called()


def test_settlement_rejects_overcharge():
    table = MagicMock()

    table.get_entity.return_value = record(
        "reservation",
        state="reserved",
        reservedCents=100,
        projectId=uid(),
    )

    with pytest.raises(FinanceDenied):
        settle(table, uid(), 101)

    table.submit_transaction.assert_not_called()


def test_unauthorized_budget_request():
    table = MagicMock()
    actor = Actor(uid(), True)

    with pytest.raises(FinanceDenied):
        request_increase(
            table,
            actor,
            uid(),
            5000,
            uid(),
            role=ProjectRole.MEMBER,
        )

    table.submit_transaction.assert_not_called()


def test_approved_request_requires_two_admins():
    table = MagicMock()
    requester = uid()
    first = uid()
    second = uid()
    request_id = uid()

    request = record(
        "increase:" + request_id,
        status="pending",
        requesterId=requester,
        firstApproverId=first,
        secondApproverId="",
        scope="global:fal",
        proposedCents=10000,
    )

    limit = record(
        "global:fal",
        ceilingCents=5000,
        spentCents=0,
        reservedCents=0,
    )

    def fetch(partition_key, row_key):
        if row_key.startswith("increase:"):
            return request
        return limit

    table.get_entity.side_effect = fetch

    result = approve_increase(
        table,
        Actor(second, True),
        request_id,
        lambda user: True,
    )

    assert result == "approved"
    actions = table.submit_transaction.call_args.args[0]
    assert len(actions) == 3
    assert actions[1][1]["ceilingCents"] == 10000


def test_duplicate_approval_rejected():
    table = MagicMock()
    approver = uid()

    table.get_entity.return_value = record(
        "increase",
        status="pending",
        requesterId=uid(),
        firstApproverId=approver,
    )

    with pytest.raises(FinanceDenied):
        approve_increase(
            table,
            Actor(approver, True),
            uid(),
            lambda user: True,
        )

    table.submit_transaction.assert_not_called()


def test_requester_cannot_approve():
    table = MagicMock()
    requester = uid()

    table.get_entity.return_value = record(
        "increase",
        status="pending",
        requesterId=requester,
        firstApproverId="",
    )

    with pytest.raises(FinanceDenied):
        approve_increase(
            table,
            Actor(requester, True),
            uid(),
            lambda user: True,
        )

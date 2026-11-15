"""Deterministic in-memory ETag/table-transaction simulations.

These are NOT tests against Azure infrastructure. They check the
application's conditional write expectations and safety invariants.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError

from finance_controls import reserve, FinanceDenied, scope_key
from project_access import Actor
from finance_controls import approve_increase


def uid():
    return str(uuid4())


class Row(dict):
    def __init__(self, data, version):
        super().__init__(data)
        self.metadata = {"etag": str(version)}


class SimulatedTable:
    def __init__(self, records):
        self.records = {k: dict(v) for k, v in records.items()}
        self.versions = {k: 1 for k in records}
        self.mutex = Lock()

    def get_entity(self, *, partition_key, row_key):
        with self.mutex:
            if row_key not in self.records:
                from azure.core.exceptions import ResourceNotFoundError
                raise ResourceNotFoundError("Not found")
            return Row(self.records[row_key], self.versions[row_key])

    def submit_transaction(self, operations):
        with self.mutex:
            # All constraints must be verified before any mutation.
            keys = []
            for op in operations:
                kind, entity = op[:2]
                key = entity["RowKey"]
                if key in keys:
                    raise HttpResponseError("duplicate row in transaction")
                keys.append(key)
                if kind == "create" and key in self.records:
                    raise HttpResponseError("already exists")
                if kind == "update":
                    if key not in self.records or (
                        op[2]["etag"] != str(self.versions[key])
                    ):
                        raise HttpResponseError("ETag mismatch")
            for op in operations:
                kind, entity = op[:2]
                key = entity["RowKey"]
                self.records[key] = dict(entity)
                self.versions[key] = self.versions.get(key, 0) + 1


def initial(project, ceiling=100):
    return {
        scope_key(project): {
            "PartitionKey": "render-finance-v1",
            "RowKey": scope_key(project),
            "ceilingCents": ceiling, "spentCents": 0, "reservedCents": 0,
        },
        "global:fal": {
            "PartitionKey": "render-finance-v1",
            "RowKey": "global:fal",
            "ceilingCents": ceiling, "spentCents": 0, "reservedCents": 0,
        },
    }


def test_concurrent_reservations_never_overspend():
    project = uid()
    table = SimulatedTable(initial(project, ceiling=100))
    job_ids = [uid() for _ in range(12)]

    def attempt(job_id):
        try:
            reserve(table, project, job_id, 30)
            return True
        except FinanceDenied:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        successes = list(executor.map(attempt, job_ids))

    assert sum(successes) <= 3
    assert table.records["global:fal"]["reservedCents"] == (
        30 * sum(successes)
    )
    assert table.records[scope_key(project)]["reservedCents"] == (
        30 * sum(successes)
    )
    assert len([k for k in table.records if k.startswith("reservation:")]) == sum(successes)


def test_duplicate_job_id_never_double_reserves():
    project, job_id = uid(), uid()
    table = SimulatedTable(initial(project, ceiling=500))
    reserve(table, project, job_id, 100)
    with pytest.raises(FinanceDenied):
        reserve(table, project, job_id, 100)
    assert table.records["global:fal"]["reservedCents"] == 100


def test_two_approvals_concurrently_cannot_double_increase():
    project = uid()
    requester, first, second, third = uid(), uid(), uid(), uid()
    request_id = uid()
    data = initial(project, ceiling=100)
    data["increase:" + request_id] = {
        "PartitionKey": "render-finance-v1",
        "RowKey": "increase:" + request_id,
        "scope": "global:fal",
        "requesterId": requester,
        "status": "pending",
        "firstApproverId": first,
        "secondApproverId": "",
        "proposedCents": 500,
    }
    table = SimulatedTable(data)

    def approve(user):
        try:
            return approve_increase(
                table, Actor(user, True), request_id,
                lambda _: True,
            ) == "approved"
        except FinanceDenied:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(approve, (second, third)))
    assert results.count(True) == 1
    assert table.records["global:fal"]["ceilingCents"] == 500
    assert table.records["increase:" + request_id]["status"] == "approved"

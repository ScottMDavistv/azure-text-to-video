"""Offline failure injection for durable at-least-once queue dispatch.

Fake storage models Azure Tables conditional ETag updates. No Azure or FAL calls.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from dispatch_recovery import (
    DispatchConflict, DispatchUncertain, LEASE_SECONDS,
    REDISPATCH_AFTER_SECONDS, dispatch_queued_job, reconcile_queued_jobs,
)


def conflict():
    err = HttpResponseError(message="ETag mismatch")
    err.status_code = 412
    return err


class Row(dict):
    def __init__(self, data, version):
        super().__init__(data)
        self.metadata = {"etag": str(version)}


class FakeTable:
    def __init__(self, job_id, status="queued"):
        self.job_id = job_id
        self.row = {"PartitionKey": "video", "RowKey": job_id,
                    "status": status}
        self.version = 1
        self.cas_calls = 0
        self.interfere = False

    def get_entity(self, *, partition_key, row_key):
        assert partition_key == "video" and row_key == self.job_id
        return Row(dict(self.row), self.version)

    def update_entity(self, *, entity, mode, etag, match_condition):
        assert mode == UpdateMode.MERGE
        assert match_condition == MatchConditions.IfNotModified
        self.cas_calls += 1
        if self.interfere or etag != str(self.version):
            raise conflict()
        self.row.update(entity)
        self.version += 1

    def query_entities(self, *, query_filter):
        assert "status eq 'queued'" in query_filter
        if self.row["status"] == "queued":
            yield Row(dict(self.row), self.version)


NOW = datetime.now(timezone.utc)


def test_success_persists_attempt_then_marks_sent():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    observed = []

    def send(received):
        assert t.row["dispatchState"] == "sending"
        assert t.row["dispatchAttempts"] == 1
        observed.append(received)

    result = dispatch_queued_job(job_id, table=t, send=send, now=NOW)
    assert result["dispatched"] is True
    assert observed == [job_id]
    assert t.row["dispatchState"] == "sent"
    assert t.cas_calls == 2


def test_immediate_retry_after_success_does_not_send():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    attempts = []
    send = attempts.append
    dispatch_queued_job(job_id, table=t, send=send, now=NOW)
    second = dispatch_queued_job(job_id, table=t, send=send,
                                 now=datetime.now(timezone.utc))
    assert second["dispatched"] is False
    assert len(attempts) == 1


def test_unknown_queue_ack_retains_lease_until_expiry():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    sends = []

    def lost_response(value):
        sends.append(value)
        raise TimeoutError("queue accepted but response lost")

    with pytest.raises(DispatchUncertain):
        dispatch_queued_job(job_id, table=t, send=lost_response, now=NOW)
    assert t.row["dispatchState"] == "sending"
    assert t.row["dispatchAttempts"] == 1
    with pytest.raises(DispatchConflict, match="lease"):
        dispatch_queued_job(job_id, table=t, send=sends.append,
                            now=NOW + timedelta(seconds=LEASE_SECONDS - 1))
    assert len(sends) == 1

    result = dispatch_queued_job(
        job_id, table=t, send=sends.append,
        now=NOW + timedelta(seconds=LEASE_SECONDS + 1))
    assert result["dispatched"] is True
    assert len(sends) == 2
    assert t.row["dispatchAttempts"] == 2


def test_concurrent_dispatch_lease_conflict_never_sends():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    t.interfere = True
    sends = []
    with pytest.raises(DispatchConflict):
        dispatch_queued_job(job_id, table=t, send=sends.append, now=NOW)
    assert sends == []


def test_failed_confirmation_remains_uncertain_not_resubmitted():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    sends = []

    def send(value):
        sends.append(value)
        t.interfere = True

    with pytest.raises(DispatchUncertain):
        dispatch_queued_job(job_id, table=t, send=send, now=NOW)
    assert sends == [job_id]
    assert t.row["dispatchState"] == "sending"
    t.interfere = False
    with pytest.raises(DispatchConflict):
        dispatch_queued_job(job_id, table=t, send=sends.append,
                            now=NOW + timedelta(seconds=10))
    assert sends == [job_id]


def test_processing_or_terminal_state_never_resends():
    for status in ["processing", "completed", "failed", "cancelled"]:
        job_id = str(uuid4())
        t = FakeTable(job_id, status=status)
        sends = []
        with pytest.raises(DispatchConflict):
            dispatch_queued_job(job_id, table=t, send=sends.append, now=NOW)
        assert not sends


def test_old_sent_marker_can_be_reconciled_without_double_claim():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    sends = []
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    dispatch_queued_job(job_id, table=t, send=sends.append, now=start)
    assert t.row["dispatchUpdatedAt"] == start.isoformat()
    old = start + timedelta(seconds=REDISPATCH_AFTER_SECONDS + 5)
    result = reconcile_queued_jobs(table=t, send=sends.append, now=old)
    assert result["inspected"] == 1
    assert result["results"][0]["outcome"] == "sent"
    assert len(sends) == 2
    # Workers still use their own ETag-guarded claim; queue duplicates are safe
    # for this non-paid processing path only if that claim remains enforced.


def test_sent_marker_retry_boundary_uses_controlled_clock():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    sends = []
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    dispatch_queued_job(job_id, table=t, send=sends.append, now=start)

    before = reconcile_queued_jobs(
        table=t, send=sends.append,
        now=start + timedelta(seconds=REDISPATCH_AFTER_SECONDS - 1))
    assert before["results"] == [{"job_id": job_id, "outcome": "already_dispatched"}]
    assert sends == [job_id]
    assert t.row["dispatchAttempts"] == 1
    assert t.cas_calls == 2

    at_boundary = reconcile_queued_jobs(
        table=t, send=sends.append,
        now=start + timedelta(seconds=REDISPATCH_AFTER_SECONDS))
    assert at_boundary["results"] == [{"job_id": job_id, "outcome": "sent"}]
    assert sends == [job_id, job_id]
    assert t.row["dispatchAttempts"] == 2
    assert t.row["dispatchUpdatedAt"] == (
        start + timedelta(seconds=REDISPATCH_AFTER_SECONDS)).isoformat()


def test_stale_sent_marker_never_resends_after_processing_claim():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    sends = []
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    dispatch_queued_job(job_id, table=t, send=sends.append, now=start)
    t.row["status"] = "processing"
    result = reconcile_queued_jobs(
        table=t, send=sends.append,
        now=start + timedelta(seconds=REDISPATCH_AFTER_SECONDS + 1))
    assert result["inspected"] == 0
    assert sends == [job_id]
    assert t.row["dispatchAttempts"] == 1


def test_recovery_sweep_held_lease_is_not_submitted():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    t.row.update(dispatchState="sending",
                 dispatchLeaseUntil=(NOW + timedelta(minutes=3)).isoformat())
    sends = []
    result = reconcile_queued_jobs(table=t, send=sends.append, now=NOW)
    assert result["results"][0]["outcome"] == "held"
    assert sends == []


def test_dispatch_attempt_cap_blocks_resubmission():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    t.row["dispatchAttempts"] = 20
    with pytest.raises(DispatchConflict, match="cap"):
        dispatch_queued_job(job_id, table=t, send=lambda _: None, now=NOW)
    assert t.cas_calls == 0


def test_recovery_batch_limit_fails_before_storage():
    for value in [True, 0, 51, "2"]:
        with pytest.raises(ValueError):
            reconcile_queued_jobs(table=None, limit=value)


def test_sweep_classifies_uncertain_outcome():
    job_id = str(uuid4())
    t = FakeTable(job_id)
    def unavailable(_):
        raise TimeoutError("no response")
    result = reconcile_queued_jobs(table=t, send=unavailable, now=NOW)
    assert result["results"] == [{"job_id": job_id, "outcome": "uncertain"}]
    assert t.row["dispatchState"] == "sending"


def test_recovery_pagination_advances_past_first_page():
    from unittest.mock import MagicMock, patch
    ids = sorted(str(uuid4()) for _ in range(3))
    table = MagicMock()

    def query(*, query_filter):
        if "RowKey gt" in query_filter:
            return iter([{"RowKey": j} for j in ids if j > ids[1]])
        return iter([{"RowKey": j} for j in ids])

    table.query_entities.side_effect = query
    with patch("dispatch_recovery.dispatch_queued_job",
               return_value={"status": "sent"}) as dispatch:
        first = reconcile_queued_jobs(table=table, now=NOW, limit=2)
        assert first["next_cursor"] == ids[1]
        second = reconcile_queued_jobs(table=table, now=NOW, limit=2,
                                       cursor=first["next_cursor"])
        assert second["next_cursor"] is None
        assert [x["job_id"] for x in first["results"] + second["results"]] == ids
        assert dispatch.call_count == 3


def test_recovery_rejects_invalid_cursor_before_storage():
    from unittest.mock import patch
    with patch("dispatch_recovery.get_jobs_table_client") as storage:
        with pytest.raises(ValueError):
            reconcile_queued_jobs(cursor="not-a-uuid")
        storage.assert_not_called()

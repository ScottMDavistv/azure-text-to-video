from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from azure.core import MatchConditions
from azure.data.tables import UpdateMode

from stale_jobs import (
    find_stale_jobs,
    is_stale,
    parse_timestamp,
    recover_stale_job,
)


NOW = datetime(
    2026, 11, 14, 20, 0, 0,
    tzinfo=timezone.utc,
)


def make_job(minutes_old, status="processing"):
    return {
        "RowKey": str(uuid4()),
        "status": status,
        "startedAt": (
            NOW - timedelta(minutes=minutes_old)
        ).isoformat(),
    }


def test_old_processing_job_is_stale():
    assert is_stale(
        make_job(120),
        now=NOW,
        stale_minutes=60,
    )


def test_recent_processing_job_is_not_stale():
    assert not is_stale(
        make_job(10),
        now=NOW,
        stale_minutes=60,
    )


def test_completed_job_is_not_stale():
    assert not is_stale(
        make_job(120, "completed"),
        now=NOW,
    )


def test_missing_timestamp_is_not_stale():
    assert not is_stale(
        {"status": "processing"},
        now=NOW,
    )


def test_timezone_required():
    with pytest.raises(ValueError):
        parse_timestamp("2026-11-14T20:00:00")


def test_invalid_stale_threshold():
    with pytest.raises(ValueError):
        is_stale(
            make_job(120),
            now=NOW,
            stale_minutes=0,
        )


def test_find_stale_jobs():
    old_job = make_job(120)
    new_job = make_job(10)

    table = MagicMock()
    table.query_entities.return_value = [
        old_job,
        new_job,
    ]

    with patch(
        "stale_jobs.get_jobs_table_client",
        return_value=table,
    ):
        results = find_stale_jobs(
            now=NOW,
            stale_minutes=60,
        )

    assert len(results) == 1
    assert results[0]["job_id"] == old_job["RowKey"]


def test_recovery_requires_confirmation():
    with pytest.raises(PermissionError):
        recover_stale_job(str(uuid4()))


def test_stale_recovery_updates_status():
    job_id = str(uuid4())

    entity = MagicMock()
    entity.get.side_effect = {
        "status": "processing",
        "startedAt": (
            NOW - timedelta(minutes=120)
        ).isoformat(),
    }.get
    entity.metadata = {"etag": 'W/"12345"'}

    table = MagicMock()
    table.get_entity.return_value = entity

    with patch(
        "stale_jobs.get_jobs_table_client",
        return_value=table,
    ):
        result = recover_stale_job(
            job_id,
            confirmed_worker_stopped=True,
            now=NOW,
        )

    assert result["status"] == "failed"

    kwargs = table.update_entity.call_args.kwargs

    assert kwargs["entity"]["status"] == "failed"
    assert kwargs["etag"] == 'W/"12345"'
    assert kwargs["mode"] == UpdateMode.MERGE
    assert kwargs["match_condition"] == (
        MatchConditions.IfNotModified
    )


def test_recent_job_cannot_be_recovered():
    job_id = str(uuid4())

    entity = MagicMock()
    entity.get.side_effect = {
        "status": "processing",
        "startedAt": (
            NOW - timedelta(minutes=5)
        ).isoformat(),
    }.get

    table = MagicMock()
    table.get_entity.return_value = entity

    with patch(
        "stale_jobs.get_jobs_table_client",
        return_value=table,
    ):
        with pytest.raises(ValueError):
            recover_stale_job(
                job_id,
                confirmed_worker_stopped=True,
                now=NOW,
            )

    table.update_entity.assert_not_called()

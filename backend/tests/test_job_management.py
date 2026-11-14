from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from job_management import (
    JobConflictError,
    cancel_job,
    get_job_details,
    list_jobs,
    normalize_job_id,
)


def make_job(status="created", job_id=None):
    return {
        "PartitionKey": "video",
        "RowKey": job_id or str(uuid4()),
        "status": status,
        "prompt": "Cinematic video",
        "createdAt": "2026-11-14T20:00:00+00:00",
    }


def make_table_entity(status, job_id):
    entity = MagicMock()
    entity.get.side_effect = make_job(
        status,
        job_id,
    ).get
    entity.metadata = {
        "etag": 'W/"test-etag"',
    }
    return entity


def test_empty_job_listing():
    table = MagicMock()
    table.query_entities.return_value = iter([])

    result = list_jobs(table=table)

    assert result["jobs"] == []
    assert result["next_cursor"] is None


def test_job_listing():
    table = MagicMock()
    first = make_job()
    second = make_job()

    table.query_entities.return_value = iter([
        first,
        second,
    ])

    result = list_jobs(
        table=table,
        limit=10,
    )

    assert result["count"] == 2
    assert result["jobs"][0]["job_id"] == (
        first["RowKey"]
    )


def test_listing_pagination():
    table = MagicMock()

    first = make_job(job_id=str(uuid4()))
    second = make_job(job_id=str(uuid4()))

    table.query_entities.return_value = iter([
        first,
        second,
    ])

    result = list_jobs(
        table=table,
        limit=1,
    )

    assert result["count"] == 1
    assert result["next_cursor"] == first["RowKey"]


def test_listing_filter():
    table = MagicMock()
    table.query_entities.return_value = iter([])

    list_jobs(
        table=table,
        status="completed",
    )

    kwargs = table.query_entities.call_args.kwargs

    assert "status eq 'completed'" in (
        kwargs["query_filter"]
    )


def test_listing_cursor_filter():
    table = MagicMock()
    table.query_entities.return_value = iter([])

    cursor = str(uuid4())

    list_jobs(
        table=table,
        cursor=cursor,
    )

    kwargs = table.query_entities.call_args.kwargs

    assert f"RowKey gt '{cursor}'" in (
        kwargs["query_filter"]
    )


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        list_jobs(
            status="anything' or status eq 'completed",
            table=MagicMock(),
        )


def test_invalid_page_size_rejected():
    with pytest.raises(ValueError):
        list_jobs(
            limit=1000,
            table=MagicMock(),
        )


def test_boolean_page_size_rejected():
    with pytest.raises(ValueError):
        list_jobs(
            limit=True,
            table=MagicMock(),
        )


def test_invalid_job_id_rejected():
    with pytest.raises(ValueError):
        normalize_job_id("../../data")


def test_get_job_details():
    job_id = str(uuid4())
    table = MagicMock()

    table.get_entity.return_value = make_job(
        status="completed",
        job_id=job_id,
    )

    result = get_job_details(
        job_id,
        table=table,
    )

    assert result["job_id"] == job_id
    assert result["status"] == "completed"


def test_cancel_created_job():
    job_id = str(uuid4())
    table = MagicMock()

    table.get_entity.return_value = (
        make_table_entity("created", job_id)
    )

    result = cancel_job(
        job_id,
        table=table,
        now=datetime(
            2026, 11, 14,
            tzinfo=timezone.utc,
        ),
    )

    assert result["status"] == "cancelled"

    kwargs = table.update_entity.call_args.kwargs

    assert kwargs["entity"]["status"] == "cancelled"
    assert kwargs["etag"] == 'W/"test-etag"'
    assert kwargs["mode"] == UpdateMode.MERGE

    assert kwargs["match_condition"] == (
        MatchConditions.IfNotModified
    )


def test_cancel_queued_job():
    job_id = str(uuid4())
    table = MagicMock()

    table.get_entity.return_value = (
        make_table_entity("queued", job_id)
    )

    result = cancel_job(
        job_id,
        table=table,
    )

    assert result["previous_status"] == "queued"


@pytest.mark.parametrize(
    "status",
    [
        "processing",
        "completed",
        "failed",
        "cancelled",
    ],
)
def test_active_or_terminal_job_cannot_cancel(status):
    job_id = str(uuid4())
    table = MagicMock()

    table.get_entity.return_value = (
        make_table_entity(status, job_id)
    )

    with pytest.raises(JobConflictError):
        cancel_job(
            job_id,
            table=table,
        )

    table.update_entity.assert_not_called()


def test_missing_etag_prevents_cancel():
    job_id = str(uuid4())
    table = MagicMock()

    entity = make_table_entity(
        "queued",
        job_id,
    )

    entity.metadata = {}

    table.get_entity.return_value = entity

    with pytest.raises(JobConflictError):
        cancel_job(
            job_id,
            table=table,
        )

    table.update_entity.assert_not_called()


def test_concurrent_claim_prevents_cancel():
    job_id = str(uuid4())
    table = MagicMock()

    table.get_entity.return_value = (
        make_table_entity("queued", job_id)
    )

    error = HttpResponseError(
        message="Precondition failed"
    )
    error.status_code = 412

    table.update_entity.side_effect = error

    with pytest.raises(
        JobConflictError,
        match="changed during cancellation",
    ):
        cancel_job(
            job_id,
            table=table,
        )

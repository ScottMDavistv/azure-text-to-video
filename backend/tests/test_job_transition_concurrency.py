"""Concurrency regression coverage for legacy job transitions.

Uses an in-memory mock; never connects to Azure or a video provider.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from jobs import JobTransitionConflict, transition_job


def make_table(status="processing", etag='W/"1"'):
    table = MagicMock()
    table.get_entity.return_value = SimpleNamespace(
        metadata={"etag": etag},
        __getitem__=None,
    )
    # Use a real dict-like entity carrying SDK metadata.
    class Entity(dict):
        pass
    row = Entity(status=status)
    row.metadata = {"etag": etag} if etag is not None else {}
    table.get_entity.return_value = row
    return table


def test_valid_transition_uses_compare_and_swap():
    table = make_table()
    job_id = str(uuid4())
    with patch("jobs.get_jobs_table_client", return_value=table):
        result = transition_job(job_id, "completed", videoUrl="safe-reference")
    assert result["status"] == "completed"
    assert result["videoUrl"] == "safe-reference"
    kwargs = table.update_entity.call_args.kwargs
    assert kwargs["etag"] == 'W/"1"'
    assert kwargs["match_condition"] == MatchConditions.IfNotModified
    assert kwargs["mode"] == UpdateMode.MERGE


@pytest.mark.parametrize("code", (409, 412))
def test_conflicting_transition_is_rejected(code):
    table = make_table()
    table.update_entity.side_effect = HttpResponseError(
        message="concurrent write", status_code=code
    )
    with patch("jobs.get_jobs_table_client", return_value=table):
        with pytest.raises(JobTransitionConflict, match="changed"):
            transition_job(str(uuid4()), "completed")


def test_missing_etag_fails_closed():
    table = make_table(etag=None)
    with patch("jobs.get_jobs_table_client", return_value=table):
        with pytest.raises(JobTransitionConflict, match="ETag"):
            transition_job(str(uuid4()), "failed")
    table.update_entity.assert_not_called()


@pytest.mark.parametrize("fields", (
    {"status": "completed"},
    {"RowKey": "other"},
    {"PartitionKey": "other"},
))
def test_callers_cannot_override_identity_or_state(fields):
    with patch("jobs.get_jobs_table_client") as get_table:
        with pytest.raises(ValueError, match="override"):
            transition_job(str(uuid4()), "failed", **fields)
        get_table.assert_not_called()


def test_illegal_transition_never_writes():
    table = make_table(status="completed")
    with patch("jobs.get_jobs_table_client", return_value=table):
        with pytest.raises(ValueError, match="Cannot transition"):
            transition_job(str(uuid4()), "queued")
    table.update_entity.assert_not_called()


def test_unexpected_storage_error_propagates():
    table = make_table()
    table.update_entity.side_effect = HttpResponseError(
        message="service unavailable", status_code=503
    )
    with patch("jobs.get_jobs_table_client", return_value=table):
        with pytest.raises(HttpResponseError):
            transition_job(str(uuid4()), "completed")

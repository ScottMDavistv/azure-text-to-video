from unittest.mock import MagicMock, patch
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.data.tables import UpdateMode

from jobs import claim_job


def make_entity(status):
    entity = MagicMock()
    entity.__getitem__.side_effect = {
        "status": status,
        "prompt": "Cinematic sunset",
    }.__getitem__
    entity.metadata = {"etag": 'W/"12345"'}
    return entity


def test_successful_claim():
    job_id = str(uuid4())
    table = MagicMock()
    table.get_entity.return_value = make_entity("queued")

    with patch("jobs.get_jobs_table_client", return_value=table):
        result = claim_job(job_id)

    assert result is not None

    kwargs = table.update_entity.call_args.kwargs
    assert kwargs["entity"]["status"] == "processing"
    assert kwargs["mode"] == UpdateMode.MERGE
    assert kwargs["etag"] == 'W/"12345"'
    assert kwargs["match_condition"] == MatchConditions.IfNotModified


def test_already_processing_is_skipped():
    job_id = str(uuid4())
    table = MagicMock()
    table.get_entity.return_value = make_entity("processing")

    with patch("jobs.get_jobs_table_client", return_value=table):
        result = claim_job(job_id)

    assert result is None
    table.update_entity.assert_not_called()


def test_completed_job_is_skipped():
    job_id = str(uuid4())
    table = MagicMock()
    table.get_entity.return_value = make_entity("completed")

    with patch("jobs.get_jobs_table_client", return_value=table):
        result = claim_job(job_id)

    assert result is None


def test_concurrent_claim_conflict():
    job_id = str(uuid4())
    table = MagicMock()
    table.get_entity.return_value = make_entity("queued")

    conflict = HttpResponseError(
        message="ETag mismatch",
    )
    conflict.status_code = 412
    table.update_entity.side_effect = conflict

    with patch("jobs.get_jobs_table_client", return_value=table):
        result = claim_job(job_id)

    assert result is None


def test_unexpected_storage_error_propagates():
    job_id = str(uuid4())
    table = MagicMock()
    table.get_entity.return_value = make_entity("queued")

    table.update_entity.side_effect = HttpResponseError(
        message="Storage unavailable",
        status_code=503,
    )

    with patch("jobs.get_jobs_table_client", return_value=table):
        try:
            claim_job(job_id)
            assert False, "Expected storage error"
        except HttpResponseError:
            pass

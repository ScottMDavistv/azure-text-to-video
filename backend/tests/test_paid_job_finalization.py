from unittest.mock import MagicMock, patch
from uuid import uuid4
import pytest
from paid_job_finalization import (
    FinalizationBlocked, finalize_paid_job, classify_paid_job_recovery,
)


def uid():
    return str(uuid4())


class Record(dict):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.metadata = {"etag": 'W/"1"'}


def context():
    job_id, project_id = uid(), uid()
    jobs, finance, intent = MagicMock(), MagicMock(), MagicMock()
    jobs.get_entity.return_value = Record(
        status="processing", projectId=project_id, blobName="renders/ok.mp4",
        billingStatus="awaiting_verified_charge",
    )
    return job_id, project_id, jobs, finance, intent


def test_completion_requires_actual_settled_reservation():
    jid, pid, jobs, fin, intent = context()
    with (
        patch("paid_job_finalization.read_intent", return_value={
            "state": "submitted", "providerRequestId": "req123"
        }),
        patch("paid_job_finalization.load", return_value={
            "state": "reserved", "projectId": pid,
        }),
    ):
        with pytest.raises(FinalizationBlocked):
            finalize_paid_job(job_id=jid, jobs_table=jobs,
                              finance_table=fin, submission_table=intent)
    jobs.update_entity.assert_not_called()


def test_completion_is_conditional_after_verified_charge():
    jid, pid, jobs, fin, intent = context()
    with (
        patch("paid_job_finalization.read_intent", return_value={
            "state": "submitted", "providerRequestId": "req123"
        }),
        patch("paid_job_finalization.load", return_value={
            "state": "settled", "projectId": pid,
        }),
    ):
        result = finalize_paid_job(
            job_id=jid, jobs_table=jobs, finance_table=fin,
            submission_table=intent,
        )
    assert result["status"] == "completed"
    assert jobs.update_entity.call_args.kwargs["etag"] == 'W/"1"'
    assert jobs.update_entity.call_args.kwargs["entity"]["billingStatus"] == "settled"


def test_completed_retry_is_read_only():
    jid, pid, jobs, fin, intent = context()
    jobs.get_entity.return_value = Record(
        status="completed", billingStatus="settled", blobName="ok"
    )
    result = finalize_paid_job(
        job_id=jid, jobs_table=jobs, finance_table=fin, submission_table=intent
    )
    assert result["replayed"]
    jobs.update_entity.assert_not_called()


def test_uncertain_provider_claim_is_never_resubmitted():
    diagnosis = classify_paid_job_recovery(
        {"status": "processing"},
        {"state": "submitting"},
        {"state": "reserved"},
    )
    assert diagnosis == "manual_review_ambiguous_provider_submission"


def test_no_video_cannot_finalize():
    jid, pid, jobs, fin, intent = context()
    jobs.get_entity.return_value["blobName"] = ""
    with pytest.raises(FinalizationBlocked):
        finalize_paid_job(job_id=jid, jobs_table=jobs,
                          finance_table=fin, submission_table=intent)

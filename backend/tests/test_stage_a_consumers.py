from unittest.mock import MagicMock, patch
from uuid import uuid4
import json
import pytest

from notification_consumer import consume_notification_once, NotificationConsumeBlocked
from paid_job_recovery import inspect_paid_job, inspect_paid_jobs, RecoveryBlocked


def uid():
    return str(uuid4())


def message():
    m = MagicMock()
    m.content = json.dumps({
        "event_id": "hold:"+uid(), "event_type": "job_held",
        "project_id": uid(), "target_user_id": uid(), "job_id": uid(),
    })
    m.id, m.pop_receipt = "message-1", "receipt-1"
    return m


def test_consumer_delivers_once_then_acks():
    q, t, apply = MagicMock(), MagicMock(), MagicMock()
    q.receive_messages.return_value = [message()]
    with patch("notification_consumer.apply_notification_once", return_value="applied"):
        assert consume_notification_once(q, t, apply=apply) == "applied"
    q.delete_message.assert_called_once()


def test_consumer_prior_application_is_safe_to_ack():
    q = MagicMock()
    q.receive_messages.return_value = [message()]
    with patch("notification_consumer.apply_notification_once",
               return_value="duplicate_ignored"):
        assert consume_notification_once(q, MagicMock(), apply=MagicMock()) == "duplicate_ignored"
    q.delete_message.assert_called_once()


def test_consumer_ambiguous_claim_not_acked():
    q = MagicMock()
    q.receive_messages.return_value = [message()]
    with patch("notification_consumer.apply_notification_once",
               side_effect=RuntimeError("ambiguous")):
        with pytest.raises(RuntimeError):
            consume_notification_once(q, MagicMock(), apply=MagicMock())
    q.delete_message.assert_not_called()


def test_consumer_rejects_untrusted_envelope_before_effect():
    q, apply = MagicMock(), MagicMock()
    m = message()
    payload = json.loads(m.content)
    payload["api_key"] = "SHOULD NEVER BE SENT"
    m.content = json.dumps(payload)
    q.receive_messages.return_value = [m]
    with pytest.raises(NotificationConsumeBlocked):
        consume_notification_once(q, MagicMock(), apply=apply)
    q.delete_message.assert_not_called()
    apply.assert_not_called()


def test_inspect_paid_job_never_calls_provider():
    jid = uid()
    jobs = MagicMock()
    jobs.get_entity.return_value = {"status": "processing", "blobName": "renders/a.mp4"}
    with (
        patch("paid_job_recovery.load", return_value={"state": "settled"}),
        patch("paid_job_recovery.read_intent", return_value={"state": "submitted"}),
        patch("paid_job_recovery.finalize_paid_job",
              return_value={"status": "completed"}) as finalize,
    ):
        result = inspect_paid_job(
            job_id=jid, jobs_table=jobs, finance_table=MagicMock(),
            submission_table=MagicMock(), allow_finalize=True,
        )
    assert result["recovery"] == "ready_to_finalize"
    finalize.assert_called_once()


def test_inspect_ambiguous_submission_never_finalizes():
    jobs = MagicMock()
    jobs.get_entity.return_value = {"status": "processing"}
    with (
        patch("paid_job_recovery.load", return_value={"state": "reserved"}),
        patch("paid_job_recovery.read_intent", return_value={"state": "submitting"}),
        patch("paid_job_recovery.finalize_paid_job") as finalize,
    ):
        result = inspect_paid_job(
            job_id=uid(), jobs_table=jobs, finance_table=MagicMock(),
            submission_table=MagicMock(), allow_finalize=True,
        )
    assert result["recovery"] == "manual_review_ambiguous_provider_submission"
    finalize.assert_not_called()


def test_batch_recovery_validates_ids_before_reading_any_jobs():
    jobs = MagicMock()
    with pytest.raises(Exception):
        inspect_paid_jobs(job_ids=[uid(), "not-a-uuid"],
                          jobs_table=jobs, finance_table=MagicMock(),
                          submission_table=MagicMock())
    jobs.get_entity.assert_not_called()

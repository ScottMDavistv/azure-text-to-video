"""Offline, no-provider tests for protected FAL submit ordering."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from fal_safe_submission import (
    PaidSubmissionBlocked,
    submit_and_wait_guarded,
    submit_once,
)
from provider_submission_ledger import SubmissionBlocked


def uid():
    return str(uuid4())


def test_missing_reservation_verifier_fails_closed():
    client = MagicMock()
    with pytest.raises(PaidSubmissionBlocked):
        submit_once(MagicMock(), uid(), "Ocean", client,
                    verify_reservation=None)
    client.submit.assert_not_called()


def test_unreserved_job_never_creates_intent_or_submits():
    client = MagicMock()
    with patch("fal_safe_submission.create_intent") as create:
        with pytest.raises(PaidSubmissionBlocked):
            submit_once(MagicMock(), uid(), "Ocean", client,
                        verify_reservation=lambda _: False)
    create.assert_not_called()
    client.submit.assert_not_called()


def test_verifier_exception_blocks_provider():
    client = MagicMock()
    with patch("fal_safe_submission.create_intent") as create:
        with pytest.raises(RuntimeError):
            submit_once(MagicMock(), uid(), "Ocean", client,
                        verify_reservation=lambda _: (_ for _ in ()).throw(RuntimeError("storage unavailable")))
    create.assert_not_called()
    client.submit.assert_not_called()


def test_order_intent_claim_provider_persist():
    steps = []
    table = MagicMock()
    client = MagicMock()
    client.submit.side_effect = lambda prompt: (
        steps.append("submit") or {"request_id": "req_test"}
    )
    with (
        patch("fal_safe_submission.create_intent",
              side_effect=lambda *a: steps.append("create")),
        patch("fal_safe_submission.claim_intent",
              side_effect=lambda *a: steps.append("claim")),
        patch("fal_safe_submission.record_provider_request_id",
              side_effect=lambda *a, **kw: steps.append("record")),
    ):
        result = submit_once(
            table, uid(), "Ocean", client,
            verify_reservation=lambda _: steps.append("reserve") or True,
        )
    assert steps == ["reserve", "create", "claim", "submit", "record"]
    assert result["request_id"] == "req_test"


def test_duplicate_intent_never_submits():
    client = MagicMock()
    with patch("fal_safe_submission.create_intent",
               side_effect=SubmissionBlocked("exists")):
        with pytest.raises(SubmissionBlocked):
            submit_once(
                MagicMock(), uid(), "Ocean", client,
                verify_reservation=lambda _: True,
            )
    client.submit.assert_not_called()


def test_claim_failure_never_submits():
    client = MagicMock()
    with (
        patch("fal_safe_submission.create_intent"),
        patch("fal_safe_submission.claim_intent",
              side_effect=SubmissionBlocked("concurrent claim")),
    ):
        with pytest.raises(SubmissionBlocked):
            submit_once(
                MagicMock(), uid(), "Ocean", client,
                verify_reservation=lambda _: True,
            )
    client.submit.assert_not_called()


def test_network_uncertainty_does_not_retry():
    client = MagicMock()
    client.submit.side_effect = TimeoutError("uncertain")
    with (
        patch("fal_safe_submission.create_intent"),
        patch("fal_safe_submission.claim_intent"),
        patch("fal_safe_submission.record_provider_request_id") as record,
    ):
        with pytest.raises(TimeoutError):
            submit_once(
                MagicMock(), uid(), "Ocean", client,
                verify_reservation=lambda _: True,
            )
    client.submit.assert_called_once()
    record.assert_not_called()


def test_record_failure_does_not_resubmit():
    client = MagicMock()
    client.submit.return_value = {"request_id": "req_test"}
    with (
        patch("fal_safe_submission.create_intent"),
        patch("fal_safe_submission.claim_intent"),
        patch("fal_safe_submission.record_provider_request_id",
              side_effect=SubmissionBlocked("storage failure")),
    ):
        with pytest.raises(SubmissionBlocked):
            submit_once(
                MagicMock(), uid(), "Ocean", client,
                verify_reservation=lambda _: True,
            )
    client.submit.assert_called_once()


def test_guarded_wait_uses_one_submission():
    client = MagicMock()
    client.submit.return_value = {"request_id": "req_test"}
    with (
        patch("fal_safe_submission.create_intent"),
        patch("fal_safe_submission.claim_intent"),
        patch("fal_safe_submission.record_provider_request_id"),
        patch("fal_safe_submission.poll_until_complete",
              return_value={"status": "COMPLETED"}) as poll,
        patch("fal_safe_submission.get_completed_video",
              return_value={"video_url": "https://fal.media/test.mp4"}),
    ):
        result = submit_and_wait_guarded(
            MagicMock(), uid(), "Ocean", client,
            verify_reservation=lambda _: True,
        )
    assert result["video_url"].startswith("https://")
    client.submit.assert_called_once()
    poll.assert_called_once()

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from fal_safe_recovery import poll_existing_submission
from provider_submission_ledger import SubmissionBlocked


def uid():
    return str(uuid4())


VALID = {
    "state": "submitted",
    "providerRequestId": "req_123",
    "statusUrl": "https://queue.fal.run/test/model/requests/req_123/status",
    "responseUrl": "https://queue.fal.run/test/model/requests/req_123",
}


def test_can_resume_without_provider_submit():
    client = MagicMock()
    with (
        patch("fal_safe_recovery.read_intent", return_value=VALID),
        patch("fal_safe_recovery.poll_until_complete",
              return_value={"status": "COMPLETED"}) as poll,
        patch("fal_safe_recovery.get_completed_video",
              return_value={"video_url": "https://fal.media/video.mp4"}),
    ):
        result = poll_existing_submission(MagicMock(), uid(), client)
    assert result["video_url"].startswith("https://")
    client.submit.assert_not_called()
    poll.assert_called_once()


def test_uncertain_state_must_not_retry_provider():
    client = MagicMock()
    with patch("fal_safe_recovery.read_intent",
               return_value={**VALID, "state": "submitting"}):
        with pytest.raises(SubmissionBlocked):
            poll_existing_submission(MagicMock(), uid(), client)
    client.submit.assert_not_called()
    client.status.assert_not_called()


def test_legacy_missing_urls_requires_reconciliation():
    client = MagicMock()
    with patch("fal_safe_recovery.read_intent",
               return_value={"state": "submitted", "providerRequestId": "old"}):
        with pytest.raises(SubmissionBlocked):
            poll_existing_submission(MagicMock(), uid(), client)
    client.submit.assert_not_called()


def test_untrusted_polling_url_rejected():
    client = MagicMock()
    with patch("fal_safe_recovery.read_intent",
               return_value={**VALID, "statusUrl": "http://127.0.0.1/admin"}):
        with pytest.raises(ValueError):
            poll_existing_submission(MagicMock(), uid(), client)
    client.submit.assert_not_called()
    client.status.assert_not_called()

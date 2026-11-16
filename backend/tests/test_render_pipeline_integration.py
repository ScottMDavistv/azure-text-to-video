import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fal_renderer import FalVideoRenderer
from worker_service import run_once


TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def test_legacy_fal_pipeline_is_blocked_before_queue_or_paid_submit():
    """The old prompt-only FAL adapter is no longer an allowed worker."""
    import pytest
    queue = MagicMock()
    provider = MagicMock()
    renderer = FalVideoRenderer(provider, downloader=lambda url: TEST_MP4)
    with patch("fal_renderer.submit_and_wait") as submit:
        with pytest.raises(ValueError, match="Legacy FAL renderer"):
            run_once(renderer, queue_client=queue)
    provider.submit.assert_not_called()
    submit.assert_not_called()
    queue.receive_messages.assert_not_called()
    queue.delete_message.assert_not_called()


def test_duplicate_job_does_not_render():
    job_id = str(uuid4())

    message = MagicMock()
    message.id = "duplicate-message"
    message.pop_receipt = "receipt"
    message.dequeue_count = 1
    message.content = json.dumps({
        "job_id": job_id,
    })

    queue = MagicMock()
    queue.receive_messages.return_value = [message]

    renderer = MagicMock()

    with patch("worker.claim_job", return_value=None):
        result = run_once(
            renderer,
            queue_client=queue,
        )

    assert result["status"] == "acknowledged"
    assert result["result"]["status"] == "skipped"

    renderer.assert_not_called()
    queue.delete_message.assert_called_once()

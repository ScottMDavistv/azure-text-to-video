import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fal_renderer import FalVideoRenderer
from worker_service import run_once


TEST_MP4 = (
    b"\x00\x00\x00\x18ftypisom"
    b"\x00\x00\x02\x00isomiso2"
)


def test_queue_to_fal_to_blob_to_completion():
    job_id = str(uuid4())

    message = MagicMock()
    message.id = "message-123"
    message.pop_receipt = "receipt-123"
    message.dequeue_count = 1
    message.content = json.dumps({
        "job_id": job_id,
    })

    queue = MagicMock()
    queue.receive_messages.return_value = [message]

    renderer = FalVideoRenderer(
        MagicMock(),
        downloader=lambda url: TEST_MP4,
    )

    with (
        patch("fal_renderer.submit_and_wait") as submit,
        patch("worker.claim_job") as claim,
        patch("worker.upload_rendered_video") as upload,
        patch("worker.transition_job") as transition,
    ):
        submit.return_value = {
            "video_url": "https://fal.media/files/test.mp4"
        }

        claim.return_value = {
            "status": "queued",
            "prompt": "Cinematic ocean sunrise",
        }

        upload.return_value = f"renders/{job_id}.mp4"

        result = run_once(
            renderer,
            queue_client=queue,
        )

    assert result["status"] == "acknowledged"
    assert result["result"]["status"] == "completed"

    submit.assert_called_once()

    upload.assert_called_once_with(
        job_id,
        TEST_MP4,
    )

    transition.assert_called_once()

    assert transition.call_args.args == (
        job_id,
        "completed",
    )

    queue.delete_message.assert_called_once()


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

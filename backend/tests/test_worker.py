from unittest.mock import patch
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from main import app
from jobs import transition_job
from worker import process_job


client = TestClient(app)


def test_created_to_queued():
    job_id = str(uuid4())

    with patch("jobs.get_jobs_table_client") as mock_client:
        mock_client.return_value.get_entity.return_value = {
            "status": "created"
        }

        result = transition_job(job_id, "queued")

        assert result["status"] == "queued"
        mock_client.return_value.update_entity.assert_called_once()


def test_invalid_transition():
    job_id = str(uuid4())

    with patch("jobs.get_jobs_table_client") as mock_client:
        mock_client.return_value.get_entity.return_value = {
            "status": "completed"
        }

        with pytest.raises(ValueError):
            transition_job(job_id, "queued")


def test_queue_endpoint():
    job_id = str(uuid4())

    with (
        patch("jobs.transition_job") as mock_transition,
        patch("job_queue.enqueue_job") as mock_enqueue,
    ):
        mock_transition.return_value = {"status": "queued"}

        response = client.post(f"/jobs/{job_id}/queue")

        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert response.json()["dispatched"] is True
        mock_enqueue.assert_called_once_with(job_id)


def test_worker_completes_job():
    job_id = str(uuid4())

    with (
        patch("worker.get_jobs_table_client") as table,
        patch("worker.transition_job") as transition,
        patch("worker.upload_rendered_video") as upload,
    ):
        table.return_value.get_entity.return_value = {
            "status": "queued",
            "prompt": "Cinematic ocean",
        }

        upload.return_value = f"renders/{job_id}.mp4"

        result = process_job(
            job_id,
            lambda prompt: b"test-mp4-content",
        )

        assert result["status"] == "completed"
        assert transition.call_count == 2
        upload.assert_called_once()


def test_worker_rejects_unqueued_job():
    job_id = str(uuid4())

    with patch("worker.get_jobs_table_client") as table:
        table.return_value.get_entity.return_value = {
            "status": "created",
            "prompt": "Cinematic ocean",
        }

        with pytest.raises(ValueError):
            process_job(job_id, lambda prompt: b"test")

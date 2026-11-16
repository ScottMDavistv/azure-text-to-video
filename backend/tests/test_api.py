import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from main import app
from storage import upload_rendered_video


@pytest.fixture(autouse=True)
def local_legacy_api_behavior(monkeypatch):
    # Existing route-behavior tests run ONLY in an explicit local-dev
    # exemption. Deployment and security tests verify default-deny separately.
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root():
    response = client.get("/")
    assert response.status_code == 200


def test_create_job():
    with patch("main.get_jobs_table_client") as mock_client:
        response = client.post(
            "/jobs",
            json={"prompt": "A cinematic sunset over mountains"},
        )

        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert response.json()["job_id"]
        mock_client.return_value.create_entity.assert_called_once()


def test_reject_empty_prompt():
    response = client.post("/jobs", json={"prompt": ""})
    assert response.status_code == 422


def test_get_job():
    job_id = str(uuid4())

    with patch("main.get_jobs_table_client") as mock_client:
        mock_client.return_value.get_entity.return_value = {
            "RowKey": job_id,
            "prompt": "Ocean waves",
            "status": "created",
            "createdAt": "2026-11-14T00:00:00+00:00",
        }

        response = client.get(f"/jobs/{job_id}")

        assert response.status_code == 200
        assert response.json()["job_id"] == job_id


def test_job_not_found():
    job_id = str(uuid4())

    with patch("main.get_jobs_table_client") as mock_client:
        mock_client.return_value.get_entity.side_effect = (
            ResourceNotFoundError("Not found")
        )

        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 404


def test_blob_upload_helper():
    job_id = str(uuid4())

    with patch("storage.get_blob_container_client") as mock_client:
        mock_client.return_value.get_blob_client.return_value = (
            MagicMock()
        )

        name = upload_rendered_video(job_id, b"test-video-content")

        assert name == f"renders/{job_id}.mp4"
        mock_client.return_value.get_blob_client.return_value.upload_blob.assert_called_once()

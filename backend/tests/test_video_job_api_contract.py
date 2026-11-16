from unittest.mock import patch
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi.testclient import TestClient

from main import app


API_TOKEN = "contract-test-token-" + "x" * 40


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_DEV", "true")
    return TestClient(app)


def test_invalid_prompt_is_rejected_without_storage(client):
    with patch("main.get_jobs_table_client") as get_table:
        response = client.post("/jobs", json={"prompt": ""})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    get_table.assert_not_called()


def test_unauthorized_prompt_submission_is_rejected(monkeypatch):
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_DEV", raising=False)
    monkeypatch.setenv("API_ACCESS_TOKEN", API_TOKEN)

    with patch("main.get_jobs_table_client") as get_table:
        response = TestClient(app).post(
            "/jobs",
            json={"prompt": "A calm ocean at sunrise"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    get_table.assert_not_called()


def test_unknown_job_uses_documented_error_shape(client):
    job_id = str(uuid4())

    with patch("main.get_jobs_table_client") as get_table:
        get_table.return_value.get_entity.side_effect = (
            ResourceNotFoundError("Not found")
        )
        response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Video job not found"}


def test_invalid_queue_transition_returns_conflict_without_dispatch(client):
    job_id = str(uuid4())

    with (
        patch(
            "jobs.transition_job",
            side_effect=ValueError("Cannot transition from completed to queued"),
        ),
        patch("dispatch_recovery.dispatch_queued_job") as dispatch,
    ):
        response = client.post(f"/jobs/{job_id}/queue")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Cannot transition from completed to queued"
    }
    dispatch.assert_not_called()


def test_download_metadata_unavailable_by_default(client, monkeypatch):
    monkeypatch.delenv("ENABLE_VIDEO_DOWNLOADS", raising=False)
    monkeypatch.delenv("VIDEO_DOWNLOAD_TOKEN", raising=False)

    response = client.get(f"/jobs/{uuid4()}/video/metadata")

    assert response.status_code == 503
    assert response.json() == {"detail": "Video downloads are disabled"}

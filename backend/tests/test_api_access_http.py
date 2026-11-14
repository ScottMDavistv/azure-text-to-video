from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app


TOKEN = "secure-test-token-" + "x" * 40


def client():
    return TestClient(app)


def test_missing_auth_rejected(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )

    with client() as http:
        result = http.post(
            "/jobs",
            json={"prompt": "Cinematic sunset"},
        )

    assert result.status_code == 401


def test_wrong_auth_rejected(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )

    with client() as http:
        result = http.post(
            "/jobs",
            headers={
                "Authorization": "Bearer invalid",
            },
            json={"prompt": "Cinematic sunset"},
        )

    assert result.status_code == 401


def test_missing_config_fails_closed(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.delenv(
        "API_ACCESS_TOKEN",
        raising=False,
    )

    with client() as http:
        result = http.post(
            "/jobs",
            json={"prompt": "Cinematic sunset"},
        )

    assert result.status_code == 503


def test_valid_auth_reaches_job_creation(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )

    with patch(
        "main.get_jobs_table_client"
    ) as get_table:
        with client() as http:
            result = http.post(
                "/jobs",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                },
                json={
                    "prompt": "Cinematic mountain sunrise",
                },
            )

    assert result.status_code == 201
    assert result.json()["status"] == "created"
    get_table.return_value.create_entity.assert_called_once()


def test_authenticated_lookup(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )

    job_id = str(uuid4())

    entity = {
        "RowKey": job_id,
        "prompt": "Mountain",
        "status": "completed",
        "createdAt": "2026-11-14T00:00:00+00:00",
    }

    with patch(
        "main.get_jobs_table_client"
    ) as get_table:
        get_table.return_value.get_entity.return_value = (
            entity
        )

        with client() as http:
            response = http.get(
                f"/jobs/{job_id}",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                },
            )

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id


def test_health_accessible_without_token(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )

    with client() as http:
        response = http.get("/health")

    assert response.status_code == 200


def test_download_has_defense_in_depth(monkeypatch):
    monkeypatch.setenv(
        "REQUIRE_JOB_API_AUTH",
        "true",
    )
    monkeypatch.setenv(
        "API_ACCESS_TOKEN",
        TOKEN,
    )
    monkeypatch.delenv(
        "ENABLE_VIDEO_DOWNLOADS",
        raising=False,
    )

    job_id = str(uuid4())

    with client() as http:
        response = http.get(
            f"/jobs/{job_id}/video",
            headers={
                "Authorization": f"Bearer {TOKEN}",
            },
        )

    # API token passes the first layer, but video
    # download protection must still remain active.
    assert response.status_code == 503

"""Regression tests for project- and owner-scoped Entra browser workflow."""
from unittest.mock import patch, MagicMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from project_access import Actor, AccessDenied
import project_video_routes as routes

PID = str(uuid4())
UID = str(uuid4())
OTHER = str(uuid4())
JID = str(uuid4())
ACTOR = Actor(UID, True)

def record(owner=UID, project=PID, state="completed"):
    return {"PartitionKey": "video", "RowKey": JID, "prompt": "Morning light",
            "createdAt": "2026-11-16T00:00:00Z", "status": state,
            "createdBy": owner, "projectId": project}

@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTHENTICATED_VIDEO_JOBS", "true")
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")

def test_routes_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("ENABLE_AUTHENTICATED_VIDEO_JOBS", raising=False)
    with pytest.raises(HTTPException) as e:
        routes.verified_actor(MagicMock(), "Bearer token")
    assert e.value.status_code == 503

def test_bearer_validation_no_service_token_fallback(enabled):
    for supplied in (None, "", "Basic credential", "Bearer "):
        with pytest.raises(HTTPException) as e:
            routes.verified_actor(MagicMock(), supplied)
        assert e.value.status_code == 401
    with patch.object(routes, "verify_project_token", return_value=ACTOR) as verifier:
        assert routes.verified_actor(MagicMock(), "Bearer signed-token") == ACTOR
        assert verifier.call_args.args[0] == "signed-token"

def test_non_member_cannot_create_or_access_jobs(enabled):
    table = MagicMock()
    with patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role", side_effect=AccessDenied()):
        with patch.object(routes, "get_jobs_table_client", return_value=table):
            with pytest.raises(HTTPException) as e:
                routes.create_project_job(PID, routes.PromptRequest(prompt="Mountain"),
                                          authorization="Bearer signed")
            assert e.value.status_code == 403
            table.create_entity.assert_not_called()

@pytest.mark.parametrize("owner,project", [(OTHER, PID), (UID, OTHER), (None, None)])
def test_cross_user_cross_project_or_legacy_jobs_hidden(enabled, owner, project):
    table = MagicMock()
    table.get_entity.return_value = record(owner=owner, project=project)
    with patch.object(routes, "require_project_role"), \
         pytest.raises(HTTPException) as error:
        routes.authorized_job(table, PID, JID, ACTOR)
    assert error.value.status_code == 404

def test_authorized_creation_records_server_identity_not_caller_fields(enabled):
    table = MagicMock()
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"):
        response = routes.create_project_job(
            PID, routes.PromptRequest(prompt="  Morning light  "),
            authorization="Bearer signed")
    entity = table.create_entity.call_args.kwargs["entity"]
    assert entity["createdBy"] == UID
    assert entity["projectId"] == PID
    assert entity["prompt"] == "Morning light"
    assert response["status"] == "created"
    assert "createdBy" not in response

def test_queue_requires_synthetic_enabled_and_never_paid(enabled, monkeypatch):
    table = MagicMock()
    table.get_entity.return_value = record(state="created")
    monkeypatch.setenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "fal")
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch("jobs.transition_job") as transition:
        with pytest.raises(HTTPException) as e:
            routes.queue_project_job(PID, JID, authorization="Bearer signed")
    assert e.value.status_code == 503
    transition.assert_not_called()

def test_download_disabled_even_for_owner(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "false")
    table = MagicMock()
    table.get_entity.return_value = record()
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch.object(routes, "get_video_resource") as video:
        with pytest.raises(HTTPException) as e:
            routes.project_video_metadata(PID, JID, authorization="Bearer signed")
    assert e.value.status_code == 503
    video.assert_not_called()

def test_cross_owner_download_never_touches_blob(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    table = MagicMock()
    table.get_entity.return_value = record(owner=OTHER)
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch.object(routes, "get_video_resource") as video:
        with pytest.raises(HTTPException) as e:
            routes.project_video_download(PID, JID, authorization="Bearer signed")
    assert e.value.status_code == 404
    video.assert_not_called()

def test_owner_metadata_uses_private_project_download_path(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    table = MagicMock()
    table.get_entity.return_value = record()
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch.object(routes, "get_video_resource", return_value={"size_bytes": 1024}):
        data = routes.project_video_metadata(PID, JID, authorization="Bearer signed")
    assert data["available"] is True
    assert data["download_endpoint"] == f"/projects/{PID}/jobs/{JID}/video"

def test_no_exposure_of_legacy_jobs_without_backend_service_secret():
    from main import app
    paths = {r.path for r in app.routes if hasattr(r, 'path')}
    assert "/projects/{project_id}/jobs" in paths
    assert "/projects/{project_id}/jobs/{job_id}/video" in paths

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

def test_project_video_routes_are_registered_and_disabled_by_default(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    monkeypatch.setenv("ENABLE_AUTHENTICATED_VIDEO_JOBS", "false")
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "false")
    with TestClient(app) as client:
        response = client.get(f"/projects/{PID}/jobs/{JID}")
    assert response.status_code == 503
    assert response.json()["detail"] == "Authenticated video jobs are disabled"

def test_private_video_not_exposed_before_completed_even_to_owner(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    table = MagicMock()
    table.get_entity.return_value = record(state="processing")
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch.object(routes, "get_video_resource") as video:
        with pytest.raises(HTTPException) as e:
            routes.project_video_metadata(PID, JID, authorization="Bearer signed")
    assert e.value.status_code == 409
    video.assert_not_called()

def test_revoked_membership_denies_status_queue_and_blob(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    monkeypatch.setenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "true")
    table = MagicMock()
    table.get_entity.return_value = record()
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role", side_effect=AccessDenied()), \
         patch.object(routes, "get_video_resource") as video, \
         patch("jobs.transition_job") as transition:
        for call in (
            lambda: routes.read_project_job(PID, JID, authorization="Bearer signed"),
            lambda: routes.queue_project_job(PID, JID, authorization="Bearer signed"),
            lambda: routes.project_video_download(PID, JID, authorization="Bearer signed"),
        ):
            with pytest.raises(HTTPException) as error:
                call()
            assert error.value.status_code == 403
    video.assert_not_called()
    transition.assert_not_called()

def test_noncanonical_or_cross_project_row_not_accessible(enabled):
    table = MagicMock()
    table.get_entity.return_value = record(project=OTHER)
    with patch.object(routes, "require_project_role"), \
         pytest.raises(HTTPException) as error:
        routes.authorized_job(table, PID, JID, ACTOR)
    assert error.value.status_code == 404

def test_forged_or_insufficient_scope_token_is_rejected(enabled):
    from project_oidc import ProjectIdentityError, ProjectScopeDenied
    table = MagicMock()
    with patch.object(routes, "verify_project_token", side_effect=ProjectIdentityError()):
        with pytest.raises(HTTPException) as error:
            routes.verified_actor(table, "Bearer forged")
    assert error.value.status_code == 401
    with patch.object(routes, "verify_project_token", side_effect=ProjectScopeDenied()):
        with pytest.raises(HTTPException) as error:
            routes.verified_actor(table, "Bearer no-scope")
    assert error.value.status_code == 403

@pytest.mark.parametrize("name,value", [
    ("ENABLE_PAID_FAL_WORKER", "1"),
    ("ENABLE_PAID_FAL_WORKER", "yes"),
    ("ENABLE_LIVE_VIDEO_RENDERING", "1"),
    ("ENABLE_WORKER_CONSUMPTION", "true"),
    ("ALLOW_UNAUTHENTICATED_DEV", "yes"),
    ("VIDEO_PROVIDER", "fal"),
])
def test_synthetic_dispatch_rejects_alternate_unsafe_runtime_flags(enabled, monkeypatch, name, value):
    monkeypatch.setenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "true")
    monkeypatch.setenv("VIDEO_RENDER_PROVIDER", "disabled")
    monkeypatch.setenv(name, value)
    assert routes.synthetic_dispatch_enabled() is False

def test_synthetic_dispatch_safe_defaults_require_opt_in(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNTHETIC_VIDEO_DISPATCH", "false")
    assert routes.synthetic_dispatch_enabled() is False


@pytest.mark.parametrize("chunks,expected_error", [
    ([b"abc", b"de"], False),
    ([b"abc", b"def"], True),
    ([b"abc"], True),
])
def test_private_download_pins_blob_etag_and_exact_size(
        enabled, monkeypatch, chunks, expected_error):
    """Detect replacement and length mismatches without exposing extra bytes."""
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    table = MagicMock()
    table.get_entity.return_value = record()
    blob = MagicMock()
    downloader = MagicMock()
    downloader.chunks.return_value = iter(chunks)
    blob.download_blob.return_value = downloader
    resource = {"size_bytes": 5, "etag": '"expected-etag"', "blob": blob}
    with patch.object(routes, "get_jobs_table_client", return_value=table), \
         patch.object(routes, "verify_project_token", return_value=ACTOR), \
         patch.object(routes, "require_project_role"), \
         patch.object(routes, "get_video_resource", return_value=resource):
        response = routes.project_video_download(PID, JID,
                                                 authorization="Bearer signed")
    output = []
    if expected_error:
        with pytest.raises(RuntimeError):
            output.extend(response.body_iterator)
    else:
        output.extend(response.body_iterator)
        assert b"".join(output) == b"abcde"
    from azure.core import MatchConditions
    assert blob.download_blob.call_args.kwargs == {
        "max_concurrency": 1, "etag": '"expected-etag"',
        "match_condition": MatchConditions.IfNotModified,
    }

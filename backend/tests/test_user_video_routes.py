"""Fail-closed regression tests for opt-in per-user Entra video routes."""
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from fastapi import HTTPException

from project_access import Actor, AccessDenied
from project_oidc import ProjectIdentityError, ProjectScopeDenied
import user_video_routes as user

def uid():
    return str(uuid4())

@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_USER_VIDEO_JOBS", "true")
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    monkeypatch.delenv("ENABLE_VIDEO_DOWNLOADS", raising=False)

def assert_http(code, func, *args, **kwargs):
    with pytest.raises(HTTPException) as exc:
        func(*args, **kwargs)
    assert exc.value.status_code == code

def test_disabled_by_default_preserves_legacy_boundary(monkeypatch):
    monkeypatch.delenv("ENABLE_USER_VIDEO_JOBS", raising=False)
    monkeypatch.setenv("ENABLE_PROJECT_ROUTES", "true")
    with patch("user_video_routes.get_jobs_table_client") as table:
        assert_http(503, user.get_user_job, UUID(uid()), authorization="Bearer any")
        table.assert_not_called()

def test_project_routes_must_also_be_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_USER_VIDEO_JOBS", "true")
    monkeypatch.delenv("ENABLE_PROJECT_ROUTES", raising=False)
    assert_http(503, user.create_user_job, user.UserJobRequest(project_id=uid(), prompt="hello"), authorization="Bearer jwt")

@pytest.mark.parametrize("authorization", [None, "", "Basic token", "Bearer ", "token"])
def test_bearer_required(enabled, authorization):
    assert_http(401, user.actor_from_bearer, MagicMock(), authorization)

def test_signed_token_and_scope_fail_closed(enabled):
    table = MagicMock()
    with patch("user_video_routes.verify_project_token", side_effect=ProjectIdentityError()):
        assert_http(401, user.actor_from_bearer, table, "Bearer forged")
    with patch("user_video_routes.verify_project_token", side_effect=ProjectScopeDenied()):
        assert_http(403, user.actor_from_bearer, table, "Bearer unsigned")

def test_create_binds_owner_and_project_from_verified_server_membership(enabled):
    actor = Actor(uid(), True)
    project = uid()
    table = MagicMock()
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=actor),
        patch("user_video_routes.require_project_role") as membership,
    ):
        result = user.create_user_job(
            user.UserJobRequest(project_id=project, prompt="A safe synthetic video"),
            authorization="Bearer signed",
        )
    membership.assert_called_once()
    row = table.create_entity.call_args.kwargs["entity"]
    assert row["ownerUserId"] == actor.user_id
    assert row["projectId"] == project
    assert row["status"] == "created"
    assert row["RowKey"] == result["job_id"]

def test_unprovisioned_or_revoked_membership_blocks_write(enabled):
    table = MagicMock()
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=Actor(uid(), True)),
        patch("user_video_routes.require_project_role", side_effect=AccessDenied()),
    ):
        assert_http(403, user.create_user_job, user.UserJobRequest(project_id=uid(), prompt="hello"), authorization="Bearer signed")
    table.create_entity.assert_not_called()

@pytest.mark.parametrize("row", [
    {"status": "completed"},
    {"ownerUserId": "other", "projectId": uid(), "status": "completed"},
    {"ownerUserId": "", "projectId": uid(), "status": "completed"},
])
def test_legacy_and_cross_owner_jobs_are_not_readable(enabled, row):
    actor = Actor(uid(), True)
    table = MagicMock()
    table.get_entity.return_value = row
    with patch("user_video_routes.require_project_role") as member:
        assert_http(404, user.stored_job, table, UUID(uid()), actor)
    member.assert_not_called()

def test_missing_job_does_not_leak(enabled):
    table = MagicMock()
    table.get_entity.side_effect = ResourceNotFoundError("missing")
    assert_http(404, user.stored_job, table, UUID(uid()), Actor(uid(), True))

def test_revoked_membership_blocks_status_queue_and_download(enabled):
    actor = Actor(uid(), True)
    project, job_id = uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = {
        "ownerUserId": actor.user_id, "projectId": project, "status": "completed",
        "prompt": "test", "createdAt": "2026", "RowKey": job_id,
    }
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=actor),
        patch("user_video_routes.require_project_role", side_effect=AccessDenied()),
        patch("jobs.transition_job") as transition,
        patch("user_video_routes.get_video_resource") as blob,
    ):
        assert_http(403, user.get_user_job, UUID(job_id), authorization="Bearer signed")
        assert_http(403, user.queue_user_job, UUID(job_id), authorization="Bearer signed")
        # Download remains separately disabled and never resolves private blob.
        assert_http(503, user.user_video_metadata, UUID(job_id), authorization="Bearer signed")
    transition.assert_not_called()
    blob.assert_not_called()

def test_enabled_download_still_requires_ownership_and_membership(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    actor = Actor(uid(), True)
    project, job_id = uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = {
        "ownerUserId": actor.user_id, "projectId": project, "status": "completed",
        "RowKey": job_id,
    }
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=actor),
        patch("user_video_routes.require_project_role", side_effect=AccessDenied()),
        patch("user_video_routes.get_video_resource") as blob,
    ):
        assert_http(403, user.user_video_metadata, UUID(job_id), authorization="Bearer signed")
    blob.assert_not_called()

def test_completed_video_metadata_has_private_user_url(enabled, monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")
    actor = Actor(uid(), True)
    project, job_id = uid(), uid()
    table = MagicMock()
    table.get_entity.return_value = {"ownerUserId": actor.user_id, "projectId": project, "status": "completed"}
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=actor),
        patch("user_video_routes.require_project_role"),
        patch("user_video_routes.get_video_resource", return_value={"job_id": job_id, "size_bytes": 10}),
    ):
        result = user.user_video_metadata(UUID(job_id), authorization="Bearer signed")
    assert result["download_endpoint"] == f"/user/jobs/{job_id}/video"
    assert result["available"] is True

def test_queue_requires_owned_job_before_dispatch(enabled):
    actor = Actor(uid(), True)
    table = MagicMock()
    table.get_entity.return_value = {"ownerUserId": uid(), "projectId": uid(), "status": "created"}
    with (
        patch("user_video_routes.get_jobs_table_client", return_value=table),
        patch("user_video_routes.verify_project_token", return_value=actor),
        patch("jobs.transition_job") as transition,
        patch("dispatch_recovery.dispatch_queued_job") as dispatch,
    ):
        assert_http(404, user.queue_user_job, UUID(uid()), authorization="Bearer signed")
    transition.assert_not_called()
    dispatch.assert_not_called()

def test_existing_service_token_routes_are_not_modified():
    from api_access import protected_job_path
    assert protected_job_path("/jobs") is True
    assert protected_job_path("/jobs/" + uid()) is True
    assert protected_job_path("/user/jobs") is False

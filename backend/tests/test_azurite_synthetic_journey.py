"""Offline prompt-to-MP4 journey using real Azurite and FFmpeg.

Start Azurite on loopback ports 10000–10002, then run from the repo root:
AZURITE_SYNTHETIC_TEST=1 PYTHONPATH=backend python -m pytest -q -s \
    backend/tests/test_azurite_synthetic_journey.py
"""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from functools import partial
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobClient, BlobServiceClient
from azure.storage.queue import QueueClient
from fastapi.testclient import TestClient

import dispatch_recovery
import job_queue
import jobs
import project_video_routes
import storage
import synthetic_worker_cli
from main import app
from project_access import Actor
from synthetic_job_renderer import SyntheticRenderError, render_scene_plan

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_SYNTHETIC_TEST") != "1",
    reason="Requires opt-in local Azurite (Blob, Queue, Table) and FFmpeg/ffprobe",
)


@pytest.fixture
def journey(monkeypatch):
    suffix = uuid4().hex
    table_name = "Synth" + suffix
    queue_name = "synthetic-" + suffix
    connection = "UseDevelopmentStorage=true"
    project_id, user_id = str(uuid4()), str(uuid4())

    for flag in ("ENABLE_AUTHENTICATED_VIDEO_JOBS", "ENABLE_PROJECT_ROUTES",
                 "ENABLE_SYNTHETIC_VIDEO_DISPATCH"):
        monkeypatch.setenv(flag, "true")
    for flag in ("ENABLE_PAID_FAL_WORKER", "ENABLE_LIVE_VIDEO_RENDERING",
                 "ENABLE_WORKER_CONSUMPTION", "ALLOW_UNAUTHENTICATED_DEV"):
        monkeypatch.setenv(flag, "false")
    for provider in ("VIDEO_RENDER_PROVIDER", "VIDEO_PROVIDER"):
        monkeypatch.setenv(provider, "disabled")
    monkeypatch.setenv("AZURE_SYNTHETIC_JOBS_QUEUE", queue_name)

    with (
        TableServiceClient.from_connection_string(connection) as tables,
        QueueClient.from_connection_string(connection, queue_name,
                                           api_version="2023-11-03") as queue,
        BlobServiceClient.from_connection_string(connection,
                                                 api_version="2023-11-03") as blobs,
    ):
        table = tables.create_table(table_name)
        try:
            queue.create_queue()
            try:
                container = blobs.create_container("private-" + suffix)
                try:
                    # Replace only the service/identity boundaries. All state
                    # transitions, queue I/O, rendering and blob I/O are real.
                    for module in (jobs, project_video_routes, synthetic_worker_cli):
                        monkeypatch.setattr(module, "get_jobs_table_client", lambda: table)
                    for module in (job_queue, synthetic_worker_cli):
                        monkeypatch.setattr(module, "get_synthetic_queue_client", lambda: queue)
                    monkeypatch.setattr(storage, "get_blob_container_client", lambda: container)
                    monkeypatch.setattr(project_video_routes, "verify_project_token",
                                        lambda token, store: Actor(user_id, True))
                    monkeypatch.setattr(project_video_routes, "require_project_role",
                                        lambda *args: None)
                    with TestClient(app, headers={"Authorization": "Bearer offline-fixture"}) as client:
                        yield client, f"/projects/{project_id}/jobs", table, queue, container
                finally:
                    container.delete_container()
            finally:
                queue.delete_queue()
        finally:
            tables.delete_table(table_name)


def accept_prompt(client, base):
    response = client.post(base, json={"prompt": "Morning light over a lake"})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "created"
    job_id = response.json()["job_id"]
    return job_id, f"{base}/{job_id}"


def test_prompt_to_private_mp4_and_retries(journey, tmp_path):
    client, base, table, queue, container = journey
    job_id, job_url = accept_prompt(client, base)
    queue_url = f"{job_url}/queue"
    response = client.post(queue_url)
    assert response.status_code == 200, response.text
    assert response.json()["dispatched"] is True

    # An API retry is not a recovery sweep, even beyond the generic
    # redispatch window. Inspect the actual queue and persisted attempt count.
    aged = datetime.now(timezone.utc) - timedelta(
        seconds=dispatch_recovery.REDISPATCH_AFTER_SECONDS + 1)
    table.update_entity({"PartitionKey": "video", "RowKey": job_id,
                         "dispatchUpdatedAt": aged.isoformat()})
    retry = client.post(queue_url)
    assert retry.status_code == 200, retry.text
    assert retry.json()["dispatched"] is False
    assert table.get_entity("video", job_id)["dispatchAttempts"] == 1
    messages = queue.peek_messages(max_messages=32)
    assert len(messages) == 1
    assert job_queue.parse_job_message(messages[0].content) == job_id

    result = synthetic_worker_cli.run_once()
    assert result["status"] == "acknowledged"
    assert result["result"]["status"] == "completed"
    assert client.get(job_url).json()["status"] == "completed"
    assert synthetic_worker_cli.run_once()["status"] == "empty"

    blob_name = f"renders/{job_id}.mp4"
    assert table.get_entity("video", job_id)["blobName"] == blob_name
    blob = container.get_blob_client(blob_name)
    properties = blob.get_blob_properties()
    assert properties.content_settings.content_type == "video/mp4"
    with BlobClient.from_blob_url(blob.url, retry_total=0) as anonymous:
        with pytest.raises(HttpResponseError) as denied:
            anonymous.download_blob()
    assert denied.value.status_code in {401, 403, 404}

    mp4 = tmp_path / "published.mp4"
    mp4.write_bytes(blob.download_blob().readall())
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_name,codec_type", "-of", "json", str(mp4),
    ], text=True, timeout=30))
    assert float(probe["format"]["duration"]) == pytest.approx(300, abs=0.6)
    assert {("video", "h264"), ("audio", "aac")} <= {
        (stream["codec_type"], stream["codec_name"]) for stream in probe["streams"]
    }
    # Decode both complete streams, not only their container headers.
    subprocess.run([
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(mp4),
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
    ], check=True, timeout=60)
    print("Published MP4 ffprobe:", json.dumps(probe, sort_keys=True))

    # Replay the same queue payload to simulate at-least-once delivery.
    queue.send_message(messages[0].content)
    replay = synthetic_worker_cli.run_once()
    assert replay["status"] == "acknowledged"
    assert replay["result"]["status"] == "skipped"
    assert list(container.list_blob_names()) == [blob_name]
    assert blob.get_blob_properties().etag == properties.etag
    assert client.post(queue_url).status_code == 409
    assert synthetic_worker_cli.run_once()["status"] == "empty"


def test_worker_ffmpeg_failure_is_visible_and_not_republished(journey):
    client, base, table, queue, container = journey
    job_id, job_url = accept_prompt(client, base)
    assert client.post(f"{job_url}/queue").json()["dispatched"] is True
    payload = queue.peek_messages(max_messages=1)[0].content

    def failing_ffmpeg(args, **kwargs):
        assert args[0] == "ffmpeg"
        return subprocess.run(["ffmpeg", "-ticket86-invalid-option"], **kwargs)

    with pytest.raises(SyntheticRenderError, match="rendering or inspection failed"):
        synthetic_worker_cli.run_once(renderer=partial(render_scene_plan, runner=failing_ffmpeg))
    row = table.get_entity("video", job_id)
    assert row["failedAt"]
    assert row["errorType"] == "SyntheticRenderError"
    status = client.get(job_url).json()
    assert status["status"] == "failed"
    assert status["error_type"] == "RenderFailed"

    assert client.post(f"{job_url}/queue").status_code == 409
    assert table.get_entity("video", job_id)["dispatchAttempts"] == 1
    assert not queue.peek_messages()
    queue.send_message(payload)
    replay = synthetic_worker_cli.run_once()
    assert replay["status"] == "acknowledged"
    assert replay["result"]["status"] == "skipped"
    assert list(container.list_blob_names()) == []


def test_queue_send_lost_ack_does_not_immediately_redispatch(journey, monkeypatch):
    client, base, table, queue, _ = journey
    job_id, job_url = accept_prompt(client, base)
    original_send = queue.send_message

    def send_then_lose_ack(payload):
        original_send(payload)
        raise ConnectionError("simulated lost acknowledgement after queue accepted send")

    monkeypatch.setattr(queue, "send_message", send_then_lose_ack)
    assert client.post(f"{job_url}/queue").status_code == 503
    assert client.post(f"{job_url}/queue").status_code == 409
    row = table.get_entity("video", job_id)
    assert row["dispatchState"] == "sending"
    assert row["dispatchAttempts"] == 1
    assert [job_queue.parse_job_message(m.content) for m in queue.peek_messages()] == [job_id]

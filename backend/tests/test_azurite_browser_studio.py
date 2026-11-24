"""Real Chromium -> studio -> project API -> Azurite/FFmpeg, with test identity."""
import os
import re
import select
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Event

import pytest
import uvicorn

import project_video_routes
import storage
import synthetic_worker_cli
import video_routes
from main import app
from project_access import Actor
from project_models import Project, ProjectRole
from project_oidc import ProjectIdentityError
from project_security import require_project_role
from project_store import add_membership, create_project
from synthetic_job_renderer import SyntheticRenderError, render_scene_plan
from test_azurite_synthetic_journey import journey  # noqa: F401 - shared emulator fixture

pytestmark = pytest.mark.skipif(
    os.getenv("AZURITE_BROWSER_TEST") != "1",
    reason="Opt in with local Azurite, Chromium, Playwright, and FFmpeg",
)
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
USER_ID = "c7e6a845-cb0c-468c-9a18-1e9df0b20545"


@pytest.fixture
def studio(journey, monkeypatch):
    from playwright.sync_api import sync_playwright

    _, base, table, queue, container = journey
    project_id = base.split("/")[2]
    create_project(table, Project(project_id, "Local test project", USER_ID))
    add_membership(table, project_id, USER_ID, ProjectRole.OWNER)

    def verify_fixture(token, _table):
        if token != "offline-mocked-account-A":
            raise ProjectIdentityError("Unknown local test identity")
        return Actor(USER_ID, True)

    monkeypatch.setattr(project_video_routes, "verify_project_token", verify_fixture)
    monkeypatch.setattr(project_video_routes, "require_project_role", require_project_role)
    monkeypatch.setattr(storage, "get_jobs_table_client", lambda: table)
    monkeypatch.setattr(storage, "get_credential", lambda: None)
    monkeypatch.setenv("ENABLE_VIDEO_DOWNLOADS", "true")

    class LocalBlobService:
        def __init__(self, **_kwargs):
            pass

        def get_blob_client(self, *, container, blob):
            assert container == local_container.container_name
            return local_container.get_blob_client(blob)

    local_container = container
    monkeypatch.setenv("AZURE_BLOB_CONTAINER", container.container_name)
    monkeypatch.setattr(video_routes, "BlobServiceClient", LocalBlobService)

    # Socket reservation avoids hard-coded API ports or accidentally talking
    # to a running service. Only frontend and API loopback ports are exposed.
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        api = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        running = pool.submit(api.run, sockets=[listener])
        try:
            with subprocess.Popen(
                ["node", "e2e/serve-synthetic-studio.mjs", str(port)],
                cwd=FRONTEND, stdout=subprocess.PIPE, text=True,
            ) as frontend:
                try:
                    assert select.select([frontend.stdout], [], [], 20)[0], "Vite did not start"
                    frontend_port = int(frontend.stdout.readline())
                    with sync_playwright() as pw:
                        chrome = os.getenv("CHROME_BIN") or shutil.which("chromium")
                        browser = pw.chromium.launch(executable_path=chrome)
                        try:
                            page = browser.new_page(accept_downloads=True)
                            page.goto(f"http://127.0.0.1:{frontend_port}")
                            page.get_by_role("button", name="Sign in with Microsoft").click()
                            page.get_by_text("Signed in: Account A").wait_for()
                            page.get_by_label("Authorized project ID").fill(project_id)
                            yield page, base, table, queue, container
                        finally:
                            browser.close()
                finally:
                    frontend.terminate()
                    frontend.wait(timeout=10)
        finally:
            api.should_exit = True
            running.result(timeout=10)


def job_status(page):
    return page.get_by_role("region", name="Live job status").get_by_role("status")


def submit_prompt(page, base, table, *, queue_status=200):
    from playwright.sync_api import expect

    prompt = "Morning light over a lake"
    page.get_by_label("Video prompt").fill(prompt)
    with (
        page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(base)) as created,
        page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/queue")) as queued,
    ):
        page.get_by_role("button", name="Submit authenticated job").click()
    assert created.value.status == 201
    assert queued.value.status == queue_status
    job = created.value.json()
    assert job["prompt"] == prompt
    expect(job_status(page)).to_have_text("Status: queued")
    row = table.get_entity("video", job["job_id"])
    assert row["createdBy"] == USER_ID
    assert row["projectId"] == base.split("/")[2]
    assert row["dispatchAttempts"] == 1
    return job["job_id"]


def test_studio_plays_and_downloads_private_worker_artifact(studio, tmp_path):
    from playwright.sync_api import expect

    page, base, table, queue, container = studio
    job_id = submit_prompt(page, base, table)
    assert len(queue.peek_messages()) == 1

    release = Event()

    def render(plan):
        # The real worker claims the job before this barrier. Let the browser
        # observe processing via normal API polling before FFmpeg can finish.
        if not release.wait(20):
            raise TimeoutError("Browser did not observe processing")
        return render_scene_plan(plan)

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(synthetic_worker_cli.run_once, renderer=render)
        try:
            expect(job_status(page)).to_have_text("Status: processing", timeout=12000)
            expect(page.get_by_role("button", name="Retrieve private MP4")).to_have_count(0)
        finally:
            release.set()
        assert worker.result(timeout=90)["result"]["status"] == "completed"

    expect(job_status(page)).to_have_text("Status: completed", timeout=15000)
    video_path = f"{base}/{job_id}/video"
    with page.expect_response(lambda r: r.url.endswith(video_path)) as response:
        page.get_by_role("button", name="Retrieve private MP4").click()
    assert response.value.status == 200
    assert response.value.headers["content-type"] == "video/mp4"
    assert response.value.headers["cache-control"] == "private, no-store"
    assert page.request.get(page.url.rstrip("/") + video_path).status == 401
    video = page.locator("video")
    expect(video).to_have_attribute("src", re.compile(r"^blob:"))
    video.evaluate("(video) => { video.muted = true; return video.play(); }")
    page.wait_for_function("document.querySelector('video').currentTime > 0")
    duration = video.evaluate("(video) => video.duration")
    assert abs(duration - 300) <= 0.6
    link = page.get_by_role("link", name="Download authorized MP4")
    with page.expect_download() as download:
        link.click()
    assert download.value.suggested_filename == f"{job_id}.mp4"
    path = tmp_path / "download.mp4"
    download.value.save_as(path)
    blob = container.get_blob_client(f"renders/{job_id}.mp4")
    assert path.read_bytes() == blob.download_blob().readall()
    print(f"Browser played a {duration:.3f}s private MP4 and downloaded identical bytes")


def test_studio_shows_safe_worker_failure_without_video(studio):
    from playwright.sync_api import expect

    page, base, table, _, container = studio
    job_id = submit_prompt(page, base, table)

    def fail_ffmpeg(args, **kwargs):
        assert args[0] == "ffmpeg"
        return subprocess.run(["ffmpeg", "-invalid-studio-test-option"], **kwargs)

    with pytest.raises(SyntheticRenderError):
        synthetic_worker_cli.run_once(renderer=partial(render_scene_plan, runner=fail_ffmpeg))
    assert table.get_entity("video", job_id)["errorType"] == "SyntheticRenderError"
    expect(job_status(page)).to_have_text("Status: failed", timeout=12000)
    expect(page.get_by_role("alert")).to_have_text("The job failed. Review server-side evidence before retrying.")
    expect(page.get_by_text("SyntheticRenderError")).to_have_count(0)
    expect(page.get_by_role("button", name="Retrieve private MP4")).to_have_count(0)
    expect(page.get_by_role("link", name="Download authorized MP4")).to_have_count(0)
    expect(page.locator("video")).to_have_count(0)
    assert list(container.list_blob_names()) == []


def test_studio_queue_retry_preserves_dispatch_lease(studio, monkeypatch):
    from playwright.sync_api import expect

    page, base, table, queue, _ = studio
    send = queue.send_message

    def lose_ack(payload):
        send(payload)
        raise ConnectionError("Test fixture lost acknowledgement")

    monkeypatch.setattr(queue, "send_message", lose_ack)
    job_id = submit_prompt(page, base, table, queue_status=503)
    retry = page.get_by_role("button", name="Retry synthetic queue")
    expect(retry).to_be_enabled()
    with page.expect_response(lambda r: r.url.endswith(f"{base}/{job_id}/queue")) as response:
        retry.click()
    assert response.value.status == 409
    assert table.get_entity("video", job_id)["dispatchAttempts"] == 1
    assert len(queue.peek_messages()) == 1
    expect(page.get_by_text("Test fixture lost acknowledgement")).to_have_count(0)

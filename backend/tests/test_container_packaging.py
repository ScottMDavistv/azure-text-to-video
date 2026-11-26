"""Validate Dockerfile COPY payloads without building images or fetching inputs.

Set PACKAGING_AZURITE_BIN to an already installed Azurite executable for the
loopback API/worker journey. It runs in isolated Python subprocesses against
the separately staged API and worker payloads, not the backend source directory.
"""
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import secrets
import shutil
import socket
import subprocess
import sys
import time
from base64 import b64encode
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TEST = Path(__file__).resolve()
ACCOUNT = "packagingfixture"
TABLE = "PackagingJobs"
QUEUE = "packaging-synthetic"
CONTAINER = "packaging-private"
WORKER_DEFAULT = ["python", "worker_runtime.py", "--check"]


def instructions(dockerfile, instruction):
    lines = (line.split(None, 1) for line in dockerfile.read_text().splitlines())
    return [parts[1] for parts in lines if parts and parts[0].upper() == instruction]


def payload(dockerfile):
    """Only accept the current flat COPY forms; don't silently skip new forms."""
    result = {}
    for copy in instructions(dockerfile, "COPY"):
        *sources, dest = copy.split()
        assert dest in {".", "./"} and sources
        for source in sources:
            assert source in {"requirements.txt", "*.py"} or (
                source.endswith(".py") and source[:-3].isidentifier())
            matches = sorted(BACKEND.glob(source))
            assert matches, f"{dockerfile.name}: empty COPY source {source}"
            for path in matches:
                assert path.is_file() and not path.is_symlink()
                result[path.name] = path
    return result


def imports(path):
    for node in ast.walk(ast.parse(path.read_bytes(), filename=path.name)):
        if isinstance(node, ast.Import):
            yield from (name.name.split(".")[0] for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module.split(".")[0]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            dynamic = (isinstance(fn, ast.Name) and fn.id in {"__import__", "import_module"}
                       or isinstance(fn, ast.Attribute) and fn.attr == "import_module")
            value = node.args[0].value
            if dynamic and isinstance(value, str):
                yield value.split(".")[0]


@pytest.mark.parametrize("recipe", ["Dockerfile", "Dockerfile.worker"])
def test_runtime_imports_are_in_copy_payload(recipe):
    packaged = payload(BACKEND / recipe)
    entrypoint = "main.py" if recipe == "Dockerfile" else "worker_runtime.py"
    assert {"requirements.txt", entrypoint} <= packaged.keys()
    local_modules = {path.stem for path in BACKEND.glob("*.py")}
    missing = {(path.name, mod) for path in packaged.values() if path.suffix == ".py"
               for mod in imports(path) if mod in local_modules and mod + ".py" not in packaged}
    assert not missing, sorted(missing)


def test_worker_default_cannot_consume():
    dockerfile = BACKEND / "Dockerfile.worker"
    assert [json.loads(cmd) for cmd in instructions(dockerfile, "CMD")] == [WORKER_DEFAULT]
    assert not instructions(dockerfile, "ENTRYPOINT")
    assert {"worker_runtime.py", "synthetic_worker_cli.py", "synthetic_job_renderer.py"} <= payload(dockerfile).keys()


def stage(recipe, path):
    path.mkdir()
    for name, source in payload(BACKEND / recipe).items():
        shutil.copyfile(source, path / name)
    return path


@contextmanager
def azurite(executable, directory):
    # Bind privately and disable telemetry. This credential exists only in this
    # temporary emulator, with no inherited cloud credentials or proxy settings.
    directory.mkdir()
    ports = []
    reservations = []
    try:
        for _ in range(3):
            listener = socket.socket()
            reservations.append(listener)
            listener.bind(("127.0.0.1", 0))
            ports.append(listener.getsockname()[1])
    finally:
        for listener in reservations:
            listener.close()
    key = b64encode(secrets.token_bytes(32)).decode()
    env = {"PATH": os.environ.get("PATH", os.defpath), "AZURITE_ACCOUNTS": f"{ACCOUNT}:{key}",
           "PACKAGING_KEY": key, "PACKAGING_PORTS": ",".join(map(str, ports))}
    args = [executable, "--location", str(directory), "--disableTelemetry", "--silent"]
    for service, port in zip(("blob", "queue", "table"), ports):
        args += [f"--{service}Host", "127.0.0.1", f"--{service}Port", str(port)]
    # Do not pass fixture SDK settings to the emulator, or emit credential values.
    with subprocess.Popen(args, cwd=directory, env={"PATH": env["PATH"],
                          "AZURITE_ACCOUNTS": env.pop("AZURITE_ACCOUNTS")},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        try:
            deadline = time.monotonic() + 15
            for port in ports:
                while True:
                    assert proc.poll() is None and time.monotonic() < deadline, "Azurite failed to start"
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                            break
                    except OSError:
                        time.sleep(0.05)
            yield env
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def isolated_command(mode, *args):
    return [sys.executable, "-I", "-B", str(TEST), mode, *map(str, args)]


def test_staged_api_and_worker_journey(tmp_path):
    executable = os.getenv("PACKAGING_AZURITE_BIN")
    if not executable:
        pytest.skip("Set PACKAGING_AZURITE_BIN to an already installed local Azurite executable")
    executable = Path(executable).resolve(strict=True)
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "Local FFmpeg and ffprobe required"
    api = stage("Dockerfile", tmp_path / "api")
    worker = stage("Dockerfile.worker", tmp_path / "worker")
    with azurite(str(executable), tmp_path / "azurite") as env:
        result = subprocess.run(isolated_command("journey", api, worker, tmp_path), cwd=api,
                                env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    print(result.stdout, end="")


def restrict_network(ports):
    """Fail closed on network attempts outside the named fixture sockets."""
    def audit(event, args):
        if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"}:
            if not ports or args[0] != "127.0.0.1":
                raise AssertionError("Unexpected DNS in packaging fixture")
        if event in {"socket.connect", "socket.sendto"}:
            address = args[1]
            if not isinstance(address, tuple) or address[:2] not in allowed:
                raise AssertionError("Unexpected socket destination in packaging fixture")
    allowed = {("127.0.0.1", port) for port in ports}
    sys.addaudithook(audit)


@contextmanager
def clients(ports, *, create=False):
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueClient

    blob, queue, table = (f"http://127.0.0.1:{port}/{ACCOUNT}" for port in ports)
    connection = (f"AccountName={ACCOUNT};AccountKey={os.environ['PACKAGING_KEY']};"
                  f"BlobEndpoint={blob};QueueEndpoint={queue};TableEndpoint={table}")
    with (BlobServiceClient.from_connection_string(connection, api_version="2023-11-03") as blobs,
          QueueClient.from_connection_string(connection, QUEUE, api_version="2023-11-03") as jobs_queue,
          TableServiceClient.from_connection_string(connection) as tables):
        if create:
            tables.create_table(TABLE)
            jobs_queue.create_queue()
            blobs.create_container(CONTAINER)
        yield tables.get_table_client(TABLE), jobs_queue, blobs


@contextmanager
def fixture_boundaries(ports, *, create=False):
    """Configure only service edges. No job logic or authorization is replaced."""
    import storage
    with clients(ports, create=create) as (table, queue, blobs):
        container = blobs.get_container_client(CONTAINER)
        storage.get_jobs_table_client = lambda: table
        storage.get_blob_container_client = lambda: container
        storage.get_credential = lambda: None
        import job_queue
        job_queue.get_synthetic_queue_client = lambda: queue
        yield table, queue, blobs, container


def fixtures_env():
    os.environ.update(
        ENABLE_AUTHENTICATED_VIDEO_JOBS="true", ENABLE_PROJECT_ROUTES="true",
        ENABLE_SYNTHETIC_VIDEO_DISPATCH="true", ENABLE_VIDEO_DOWNLOADS="true",
        ENABLE_PAID_FAL_WORKER="false", ENABLE_LIVE_VIDEO_RENDERING="false",
        ENABLE_WORKER_CONSUMPTION="false", ALLOW_UNAUTHENTICATED_DEV="false",
        VIDEO_PROVIDER="disabled", VIDEO_RENDER_PROVIDER="disabled",
        AZURE_SYNTHETIC_JOBS_QUEUE=QUEUE, AZURE_BLOB_CONTAINER=CONTAINER,
    )


def once(ports):
    fixtures_env()
    with fixture_boundaries(ports):
        sys.argv[:] = ["synthetic_worker_cli", "--once"]
        # Exercise the exact module __main__/argv path with only SDK endpoints replaced.
        runpy.run_module("synthetic_worker_cli", run_name="__main__", alter_sys=True)


def check_default():
    sys.argv[:] = WORKER_DEFAULT[1:]
    runpy.run_path(WORKER_DEFAULT[1], run_name="__main__")


def journey(ports, worker, directory):
    import hashlib
    from azure.core.exceptions import HttpResponseError
    from azure.storage.blob import BlobClient
    from fastapi.testclient import TestClient

    fixtures_env()
    with fixture_boundaries(ports, create=True) as (table, queue, blobs, container):
        from main import app
        from project_access import Actor
        from project_models import Project, ProjectRole
        from project_oidc import ProjectIdentityError
        from project_store import add_membership, create_project
        import project_video_routes
        import video_routes

        project, owner, member, nonmember = (str(uuid4()) for _ in range(4))
        create_project(table, Project(project, "Packaging fixture", owner))
        add_membership(table, project, member, ProjectRole.MEMBER)
        identities = {"fixture-member": member, "fixture-nonmember": nonmember}

        def verify_fixture(token, store):
            assert store is table
            if token not in identities:
                raise ProjectIdentityError("Unknown fixture identity")
            return Actor(identities[token], True)

        project_video_routes.verify_project_token = verify_fixture
        video_routes.BlobServiceClient = lambda **_: blobs
        # The real authorization and Azure SDK data paths run against the fixture table/container.
        member_header = {"Authorization": "Bearer fixture-member"}
        nonmember_header = {"Authorization": "Bearer fixture-nonmember"}
        with TestClient(app) as client:
            created = client.post(f"/projects/{project}/jobs", headers=member_header,
                                  json={"prompt": "A quiet lake at dawn"})
            assert created.status_code == 201 and created.json()["status"] == "created"
            job = created.json()["job_id"]
            route = f"/projects/{project}/jobs/{job}"
            queued = client.post(route + "/queue", headers=member_header)
            assert queued.status_code == 200
            assert queued.json() == {"job_id": job, "status": "queued", "dispatched": True}
            row = table.get_entity("video", job)
            assert (row["createdBy"], row["dispatchAttempts"]) == (member, 1)
            assert len(queue.peek_messages()) == 1

            inspected = subprocess.run(isolated_command("default", worker), cwd=worker,
                                       capture_output=True, text=True, timeout=20, check=True)
            assert isinstance(json.loads(inspected.stdout), dict)
            assert len(queue.peek_messages()) == 1 and list(container.list_blob_names()) == []
            assert table.get_entity("video", job)["status"] == "queued"
            ran = subprocess.run(isolated_command("once", worker), cwd=worker, capture_output=True,
                                 text=True, timeout=120, check=True)
            assert ran.stdout.strip() == "acknowledged"
            assert len(queue.peek_messages()) == 0
            assert sum(1 for _ in table.query_entities("PartitionKey eq 'video'")) == 1
            key = f"renders/{job}.mp4"
            assert list(container.list_blob_names()) == [key]
            stored_blob = container.get_blob_client(key)

            status, metadata, data = [client.get(route + suffix, headers=member_header)
                                      for suffix in ("", "/video/metadata", "/video")]
            assert (status.status_code, metadata.status_code, data.status_code) == (200, 200, 200)
            assert status.json()["status"] == "completed"
            assert metadata.json()["download_endpoint"] == route + "/video"
            assert metadata.json()["size_bytes"] == len(data.content)
            assert data.headers["content-type"] == "video/mp4"
            assert data.headers["cache-control"] == "private, no-store"
            assert hashlib.sha256(data.content).digest() == hashlib.sha256(
                stored_blob.download_blob().readall()).digest()
            for headers, expected, detail in (
                (nonmember_header, 403, "Project access denied"),
                ({}, 401, "Bearer token required"),
            ):
                for suffix in ("", "/video/metadata", "/video"):
                    denied = client.get(route + suffix, headers=headers)
                    assert denied.status_code == expected and denied.json() == {"detail": detail}
            with BlobClient.from_blob_url(stored_blob.url, retry_total=0) as anonymous:
                with pytest.raises(HttpResponseError) as denial:
                    anonymous.download_blob()
                assert denial.value.status_code in {401, 403, 404}

        mp4 = directory / "fixture.mp4"
        mp4.write_bytes(data.content)
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_name,codec_type", "-of", "json", str(mp4),
        ], timeout=20))
        duration = float(probe["format"]["duration"])
        assert abs(duration - 300) <= 0.6
        assert {(s["codec_type"], s["codec_name"]) for s in probe["streams"]} == {
            ("video", "h264"), ("audio", "aac")}
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(mp4),
                        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                       check=True, timeout=60)
        print("member create/queue 201/200; default no consumption; explicit --once acknowledged one job")
        print("status/metadata/bytes: member 200/200/200, nonmember 403/403/403, signed-out 401/401/401")
        print(f"private MP4 {duration:.3f}s, H.264/AAC fully decoded, delivered/stored SHA-256 matched")


if __name__ == "__main__":
    mode, stage_dir, *args = sys.argv[1:]
    sys.path.insert(0, stage_dir)
    ports = tuple(map(int, os.environ["PACKAGING_PORTS"].split(",")))
    restrict_network(() if mode == "default" else ports)
    if mode == "default":
        check_default()
    elif mode == "once":
        once(ports)
    else:
        assert mode == "journey"
        journey(ports, *map(Path, args))

"""Disposable local acceptance, no cloud resources or paid rendering."""
import asyncio
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from secrets import token_hex
import select
import shutil
import socket
import subprocess
import tarfile
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock
from concurrent.futures import ThreadPoolExecutor
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
from uuid import UUID, uuid4

from jwt.exceptions import PyJWKClientConnectionError

from fastapi import HTTPException, Request as Incoming
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

ROOT = Path.cwd()
CONTROL = "/projects/ticket90-local-check"
ORIGIN = "http://localhost:5173"
DENIALS = {401: "Bearer token required", 403: "Project access denied"}
SUFFIXES = ("", "/video/metadata", "/video")
CACHE = "private, no-store"

def registration_auth_category(error, scope_error, identity_error):
    if isinstance(error, scope_error):
        return "api-scope-missing"
    if isinstance(error, PyJWKClientConnectionError) or (
        isinstance(error, identity_error)
        and isinstance(error.__cause__, PyJWKClientConnectionError)
    ):
        return "signing-key-service-unavailable"
    if isinstance(error, (identity_error, ValueError)):
        return "token-rejected"
    return "verification-error"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def verify_http(opener, job_path, authorization, expected):
    for suffix in SUFFIXES:
        headers = {"Authorization": authorization} if authorization else {}
        req = Request(ORIGIN + job_path + suffix, headers=headers)
        try:
            with opener.open(req, timeout=15) as response:
                if response.status != expected or expected != 200:
                    return False
                if suffix == "/video" and response.headers.get("Cache-Control") != CACHE:
                    return False
        except HTTPError as exc:
            with exc:
                if exc.code != expected or expected not in DENIALS:
                    return False
                if json.loads(exc.read(4096)) != {"detail": DENIALS[expected]}:
                    return False
    return True

class RunState:
    def __init__(self):
        self.roles = {}
        self.codes = {role: token_hex(6) for role in ("member", "nonmember")}
        self.project = str(uuid4())
        self.job = None
        self.queued = Event()
        self.complete = False
        self.data_hash = None
        self.direct = set()
        self.browser = set()
        self.studio_metadata = False
        self.studio_media = False
        self.play_and_download = False

    def claim(self, code, identity):
        if not isinstance(code, str) or len(code) != 12 or identity in self.roles:
            return None
        return next((role for role, value in self.codes.items()
                     if hmac.compare_digest(code, value)), None)

    @property
    def accepted(self):
        return (self.complete and self.studio_metadata and self.studio_media
                and self.play_and_download
                and self.direct == self.browser == {"member", "nonmember", "signed-out"})

def summary(state):
    for role, status in (("member", 200), ("nonmember", 403), ("signed-out", 401)):
        for label, rows in (("api", state.direct), ("browser", state.browser)):
            value = status if role in rows else "not-verified"
            print(f"TKT90 {role}-{label}={value}", flush=True)
    print("TKT90 member-playback-download=" + ("PASS" if state.play_and_download else "not-verified"), flush=True)

def add_controls(api, state, harness, table, marker):
    from project_models import Project, ProjectRole
    from project_oidc import ProjectIdentityError, ProjectScopeDenied, verify_entra_access_claims
    from project_store import add_membership, create_project
    opener = build_opener(ProxyHandler({}), NoRedirect())
    guard = asyncio.Lock()
    enrollment_lock = Lock()
    base = f"/projects/{state.project}/jobs"
    started_create = False
    started_queue = False

    def identify(incoming, allow_signed_out=False, *, registration=False):
        authorization = incoming.headers.get("authorization", "")
        if not authorization and allow_signed_out:
            return "signed-out", ""
        scheme, _, token = authorization.partition(" ")
        if scheme != "Bearer" or not token or len(token) > 8192:
            if registration:
                print("TKT90 registration-auth=" +
                      ("bearer-missing" if not authorization else "bearer-invalid"),
                      flush=True)
            raise HTTPException(401, "Verified test sign-in required")
        try:
            identity = harness.mapping_identity(token, verify_entra_access_claims)
        except Exception as error:
            if registration:
                print("TKT90 registration-auth=" +
                      registration_auth_category(error, ProjectScopeDenied, ProjectIdentityError),
                      flush=True)
            raise HTTPException(401, "Verified test sign-in required") from None
        return identity, authorization

    def same_origin(request):
        if request.headers.get("origin") != ORIGIN:
            raise HTTPException(403, "Loopback browser origin required")

    def role_for(request):
        same_origin(request)
        identity, authorization = identify(request, allow_signed_out=True)
        role = "signed-out" if not authorization else state.roles.get(identity)
        if role is None:
            raise HTTPException(403, "Register this test session first")
        if not state.complete or state.job is None:
            raise HTTPException(409, "Local render not complete")
        return role, authorization

    @api.get(CONTROL + "/ready", response_class=PlainTextResponse)
    def ready():
        return marker

    @api.post(CONTROL + "/enroll")
    def enroll(data: dict, request: Incoming):
        same_origin(request)
        identity, _ = identify(request, registration=True)
        with enrollment_lock:
            role = state.claim(data.get("code"), identity)
            if role is None:
                raise HTTPException(403, "Invalid test session")
            del state.codes[role]
            user_id = str(uuid4())
            table.create_entity({"PartitionKey": "project-oidc-users", "RowKey": identity,
                                 "userId": user_id, "active": True})
            if role == "member":
                create_project(table, Project(state.project, "Ticket 90 disposable project", user_id))
                add_membership(table, state.project, user_id, ProjectRole.MEMBER)
            state.roles[identity] = role
            print(f"TKT90 {role}-session=verified", flush=True)
            return {"role": role, "project": state.project if role == "member" else None}

    @api.post(CONTROL + "/check")
    def check(request: Incoming):
        role, authorization = role_for(request)
        status = {"member": 200, "nonmember": 403, "signed-out": 401}[role]
        if role == "member" and not (state.studio_metadata and state.studio_media):
            raise HTTPException(409, "Retrieve private MP4 in the studio first")
        path = f"{base}/{state.job}"
        if not verify_http(opener, path, authorization, status):
            raise HTTPException(409, "Direct API result did not match")
        state.direct.add(role)
        return {"role": role, "path": path, "expected": status, "detail": DENIALS.get(status)}

    @api.post(CONTROL + "/report")
    def report(data: dict, request: Incoming):
        role, _ = role_for(request)
        status = {"member": 200, "nonmember": 403, "signed-out": 401}[role]
        if role not in state.direct or data.get("results") != [status] * 3:
            raise HTTPException(409, "Browser evidence incomplete")
        state.browser.add(role)
        return {"ok": True}

    @api.post(CONTROL + "/media-proof")
    def media_proof(data: dict, request: Incoming):
        role, _ = role_for(request)
        if (role != "member" or "member" not in state.browser or not state.data_hash
                or data.get("played") is not True or data.get("sha") != state.data_hash):
            raise HTTPException(409, "Media evidence did not match")
        state.play_and_download = True
        return {"ok": True}

    @api.middleware("http")
    async def observe(request, call_next):
        nonlocal started_create, started_queue
        path = request.url.path
        if request.method == "POST" and not path.startswith(CONTROL + "/"):
            # Only one real creation and queue operation, with both roles ready first.
            if set(state.roles.values()) != {"member", "nonmember"}:
                return JSONResponse({"detail": "Both test sessions must be ready"}, status_code=409)
            try:
                same_origin(request)
                identity, _ = await run_in_threadpool(identify, request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            if state.roles.get(identity) != "member":
                return JSONResponse({"detail": "Not the test project member"}, status_code=403)
            async with guard:
                if path == base and not started_create:
                    started_create = True
                    response = await call_next(request)
                    if response.status_code == 201:
                        # Preserve the exact production response after observing its generated ID.
                        body = b"".join([chunk async for chunk in response.body_iterator])
                        state.job = str(UUID(json.loads(body)["job_id"]))
                        print("TKT90 member-job-created=201", flush=True)
                        from starlette.responses import Response
                        return Response(body, status_code=201, headers=dict(response.headers))
                    return response
                if state.job and path == f"{base}/{state.job}/queue" and not started_queue:
                    started_queue = True
                    response = await call_next(request)
                    if response.status_code == 200:
                        print("TKT90 member-job-queued=200", flush=True)
                        state.queued.set()
                    return response
                return JSONResponse({"detail": "Only one local render is authorized"}, status_code=409)
        response = await call_next(request)
        # Browser requests for the actual studio include Sec-Fetch-Dest; exclude Python checks.
        # The dedicated browser check starts after retrieval and cannot create playback evidence.
        if (state.job and request.method == "GET" and request.headers.get("sec-fetch-dest")
                and response.status_code == 200 and "member" not in state.direct):
            if path == f"{base}/{state.job}/video/metadata":
                state.studio_metadata = True
            if path == f"{base}/{state.job}/video" and response.headers.get("cache-control") == CACHE:
                state.studio_media = True
        return response

@contextmanager
def storage_emulator(work):
    for port in (10000, 10001, 10002):
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
    hits = []
    for p in Path.home().glob(".npm/_npx/*/node_modules/azurite/package.json"):
        if json.loads(p.read_text()).get("version") == "3.35.0":
            hits.append(p.parent / "dist/src/azurite.js")
    if not hits:
        raise RuntimeError("cached Azurite missing")
    with subprocess.Popen(["node", str(sorted(hits)[0]), "--silent", "--location", str(work),
                           "--blobHost", "127.0.0.1", "--queueHost", "127.0.0.1",
                           "--tableHost", "127.0.0.1"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    for port in (10000, 10001, 10002):
                        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                            pass
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError("local Azurite did not start")
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

def main(state):
    spec = spec_from_file_location("ticket90_local", Path(__file__).with_name("local_studio.py"))
    harness = module_from_spec(spec)
    spec.loader.exec_module(harness)
    sys.path.insert(0, str(ROOT / "backend"))
    from synthetic_worker_cli import run_once

    curl = os.environ["TKT90_WINDOWS_CURL"]
    if not Path(curl).is_file():
        raise RuntimeError("Windows curl missing")
    marker = token_hex(16)
    with TemporaryDirectory(prefix="ticket90-full-") as tmp:
        scratch = Path(tmp)
        frontend = scratch / "frontend"
        # Use tracked bytes only, not arbitrary files from the staged checkout.
        paths = ["frontend/src", "frontend/index.html"]
        if subprocess.check_output(["git", "ls-tree", "HEAD", "frontend/public"]).strip():
            paths.append("frontend/public")
        with subprocess.Popen(["git", "archive", "--format=tar", "HEAD", *paths],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as tree:
            with tarfile.open(fileobj=tree.stdout, mode="r|") as archive:
                archive.extractall(scratch, filter="data")
            if tree.wait():
                raise RuntimeError("verified frontend export failed")
        # Compare only; do not modify the tested checkout or its copy of the auth module.
        from re import escape, search
        auth_src = (frontend / "src/entraAuth.ts").read_text()
        for name, value in (("tenantId", harness.TENANT), ("apiClientId", harness.API)):
            if not search(r"(?m)^const " + name + r" = '" + escape(value) + r"'\s*$", auth_src):
                print("TKT90 preflight=entra-config-does-not-match-tested-frontend", flush=True)
                return False
        (frontend / "e2e").mkdir()
        (frontend / "node_modules").symlink_to((ROOT / "frontend/node_modules").resolve())
        file = frontend / "index.html"
        html = file.read_text()
        if html.count("</body>") != 1:
            raise RuntimeError("unexpected index layout")
        file.write_text(html.replace("</body>", '<script type="module" src="/ticket90-panel.js"></script></body>'))
        shutil.copyfile(Path(__file__).with_name("panel.js"), frontend / "ticket90-panel.js")
        runner = Path(__file__).with_name("serve-local.mjs").read_text()
        if runner.count("  envDir: false,") != 1:
            raise RuntimeError("unexpected Vite launcher")
        (frontend / "e2e/serve-ticket90-local.mjs").write_text(
            runner.replace("  envDir: false,", "  envDir: false,\n  cacheDir: '.ticket90-vite-cache',"))
        harness.FRONTEND = frontend
        with (open("/dev/tty", "r") as tty, storage_emulator(scratch / "azurite"),
              harness.disposable_services() as (api, table, _queue, container),
              ThreadPoolExecutor(max_workers=1) as executor):
            add_controls(api, state, harness, table, marker)
            with harness.serve(api):
                probe = subprocess.run([curl, "-q", "--noproxy", "*", "--silent", "--fail", "--max-time", "6",
                                        ORIGIN + CONTROL + "/ready"], capture_output=True, timeout=8)
                if probe.returncode or probe.stdout != marker.encode("ascii"):
                    print("TKT90 preflight=windows-studio-unreachable", flush=True)
                    return False
                print("TKT90 windows-studio=ready commit=" + harness.BASELINE[:7], flush=True)
                print("Use these two local setup codes only in the matching Edge windows; don't share them.")
                print("Member code: " + state.codes["member"])
                print("Nonmember code: " + state.codes["nonmember"], flush=True)
                future = None
                deadline = time.monotonic() + 1200
                while time.monotonic() < deadline:
                    if state.accepted:
                        return True
                    if select.select([tty], [], [], 0.25)[0]:
                        tty.readline()
                        return False
                    if future is None and state.queued.is_set():
                        def work():
                            time.sleep(4)
                            outcome = run_once()
                            if (outcome.get("result", {}).get("status") != "completed"
                                    or str(outcome["result"].get("job_id")) != state.job):
                                raise RuntimeError("render did not complete")
                            blob = container.get_blob_client(f"renders/{state.job}.mp4")
                            return hashlib.sha256(blob.download_blob().readall()).hexdigest()
                        future = executor.submit(work)
                    if future is not None and future.done() and not state.complete:
                        state.data_hash = future.result()
                        state.complete = True
                        print("TKT90 synthetic-render=completed", flush=True)
                return False

if __name__ == "__main__":
    state = RunState()
    try:
        result = "PASS" if main(state) else "INCOMPLETE"
    except (Exception, KeyboardInterrupt):
        result = "INCOMPLETE"
    summary(state)
    print(f"TKT90 result={result}", flush=True)
    sys.exit(0 if result == "PASS" else 1)

"""Ticket #90 supervised local harness. No live mode without operator opt-in."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import UUID, uuid4

BASELINE = "606dd91663b8bc63f87773dd70988898d55572d0"
FRONTEND = Path.cwd() / "frontend"
ORIGIN = "http://localhost:5173"
TENANT = os.getenv("TKT90_TENANT", "00000000-0000-4000-8000-000000000001")
API = os.getenv("TKT90_API", "00000000-0000-4000-8000-000000000002")
SPA = os.getenv("TKT90_SPA", "00000000-0000-4000-8000-000000000003")
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
JWKS = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
CONNECTION = "UseDevelopmentStorage=true"


def mapping_identity(token, verify):
    claims = verify(token)
    if claims["issuer"] != ISSUER or claims["tenant"] != TENANT:
        raise ValueError("Unexpected verified identity")
    return hashlib.sha256((claims["issuer"] + "|" + claims["subject"]).encode()).hexdigest()


def test_flags(queue_name, container_name):
    return {
        "ENABLE_AUTHENTICATED_VIDEO_JOBS": "true",
        "ENABLE_PROJECT_ROUTES": "true",
        "ENABLE_SYNTHETIC_VIDEO_DISPATCH": "true",
        "ENABLE_VIDEO_DOWNLOADS": "true",
        "ENABLE_PAID_FAL_WORKER": "false",
        "ENABLE_LIVE_VIDEO_RENDERING": "false",
        "ENABLE_WORKER_CONSUMPTION": "false",
        "ALLOW_UNAUTHENTICATED_DEV": "false",
        "VIDEO_RENDER_PROVIDER": "disabled",
        "VIDEO_PROVIDER": "disabled",
        "AZURE_SYNTHETIC_JOBS_QUEUE": queue_name,
        "AZURE_BLOB_CONTAINER": container_name,
        "PROJECT_OIDC_ISSUER": ISSUER,
        "PROJECT_OIDC_JWKS_URL": JWKS,
        "PROJECT_OIDC_AUDIENCE": API,
    }


@contextmanager
def disposable_services():
    import job_queue
    import jobs
    import project_video_routes
    import storage
    import synthetic_worker_cli
    import video_routes
    from azure.core.pipeline.transport import RequestsTransport
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueClient
    from fastapi import FastAPI

    suffix = uuid4().hex
    names = ("Tkt90" + suffix, "synthetic-" + suffix, "private-" + suffix)
    table_name, queue_name, container_name = names
    with ExitStack() as stack:
        tables = stack.enter_context(TableServiceClient.from_connection_string(
            CONNECTION, transport=RequestsTransport(use_env_settings=False)))
        queue = stack.enter_context(QueueClient.from_connection_string(
            CONNECTION, queue_name, api_version="2023-11-03",
            transport=RequestsTransport(use_env_settings=False)))
        blobs = stack.enter_context(BlobServiceClient.from_connection_string(
            CONNECTION, api_version="2023-11-03",
            transport=RequestsTransport(use_env_settings=False)))
        table = tables.create_table(table_name)
        stack.callback(tables.delete_table, table_name)
        queue.create_queue()
        stack.callback(queue.delete_queue)
        container = blobs.create_container(container_name)
        stack.callback(container.delete_container)
        stack.enter_context(patch.dict(os.environ, test_flags(queue_name, container_name)))

        def deny_cloud_credentials():
            raise RuntimeError("Local harness refuses Azure credentials")

        class LocalBlobService:
            def __init__(self, **kwargs):
                if kwargs.get("credential") is not None:
                    raise RuntimeError("Expected only loopback blob storage")

            def get_blob_client(self, *, container, blob):
                if container != container_name:
                    raise RuntimeError("Unexpected local container")
                return local_container.get_blob_client(blob)

        local_container = container
        for module in (jobs, project_video_routes, synthetic_worker_cli, storage, job_queue):
            stack.enter_context(patch.object(module, "get_jobs_table_client", lambda: table))
        for module in (job_queue, synthetic_worker_cli):
            stack.enter_context(patch.object(module, "get_synthetic_queue_client", lambda: queue))
        stack.enter_context(patch.object(storage, "get_blob_container_client", lambda: container))
        # The production download helper passes a credential into its storage
        # constructor; this test-only adapter accepts None and uses Azurite.
        stack.enter_context(patch.object(storage, "get_credential", lambda: None))
        stack.enter_context(patch.object(job_queue, "get_credential", deny_cloud_credentials))
        stack.enter_context(patch.object(video_routes, "BlobServiceClient", LocalBlobService))
        api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        api.include_router(project_video_routes.router)
        yield api, table, queue, container


@contextmanager
def serve(api):
    import uvicorn

    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(api, log_level="critical", access_log=False))
        running = pool.submit(server.run, sockets=[listener])
        proc = None
        try:
            proc = subprocess.Popen(
                ["node", "e2e/serve-ticket90-local.mjs", str(port), SPA],
                cwd=FRONTEND, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")},
            )
            import select

            if not select.select([proc.stdout], [], [], 20)[0] or proc.stdout.readline().strip() != "READY":
                raise RuntimeError("Vite failed to bind the approved localhost port")
            yield
        finally:
            try:
                if proc is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    proc.stdout.close()
            finally:
                server.should_exit = True
                running.result(timeout=10)


class OfflineChecks(unittest.TestCase):
    def test_identity_uses_verified_issuer_and_subject_not_email(self):
        claims = {"issuer": ISSUER, "tenant": TENANT, "subject": "internal-fixture-only"}
        one = mapping_identity("offline-placeholder", lambda _: {**claims, "email": "first"})
        two = mapping_identity("offline-placeholder", lambda _: {**claims, "email": "second"})
        self.assertEqual(one, two)
        self.assertNotEqual(one, mapping_identity("offline-placeholder",
                                                  lambda _: {**claims, "subject": "different"}))
        with self.assertRaises(ValueError):
            mapping_identity("offline-placeholder", lambda _: {**claims, "tenant": "other"})
        with self.assertRaises(ValueError):
            mapping_identity("offline-placeholder", lambda _: {**claims, "issuer": "other"})

    def test_storage_and_provider_flags_are_fixed(self):
        self.assertEqual(CONNECTION, "UseDevelopmentStorage=true")
        flags = test_flags("synthetic-local", "private-local")
        for flag in ("ENABLE_PAID_FAL_WORKER", "ENABLE_LIVE_VIDEO_RENDERING",
                     "ENABLE_WORKER_CONSUMPTION", "ALLOW_UNAUTHENTICATED_DEV"):
            self.assertEqual(flags[flag], "false")
        for flag in ("VIDEO_RENDER_PROVIDER", "VIDEO_PROVIDER"):
            self.assertEqual(flags[flag], "disabled")
        self.assertEqual(flags["PROJECT_OIDC_JWKS_URL"], JWKS)
        self.assertEqual(flags["PROJECT_OIDC_AUDIENCE"], API)


if __name__ == "__main__":
    if sys.argv[1:] != ["--offline-self-test"]:
        sys.exit("This helper only supports --offline-self-test; use launch.sh for a supervised run.")
    unittest.main(argv=[sys.argv[0]])

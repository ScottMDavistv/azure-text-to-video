"""Offline negative-path tests for authenticated Azure inventory artifact handoff."""
import datetime as dt
import importlib.util
import io
import json
import pathlib
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verified_inventory", ROOT / "scripts/verified_azure_inventory.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)
INVENTORY = {"status": "inventory only — runtime health not asserted",
             "resource_group": "rg-textvideo-dev", "resources": []}


def archive(payload=INVENTORY):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("azure-inventory.json", json.dumps(payload))
    return buf.getvalue()


def fixture(*, run_overrides=None, job_success=True, artifact=None):
    run = {"id": 55, "conclusion": "success", "head_branch": "main",
           "event": "schedule", "path": ".github/workflows/azure-readonly-operations.yml@main",
           "repository": {"full_name": "ScottMDavistv/azure-text-to-video"},
           "created_at": "2026-11-17T11:00:00Z", "head_sha": "a" * 40}
    run.update(run_overrides or {})
    def read(path, token, binary=False):
        if path.endswith("runs?branch=main&per_page=10"):
            return {"workflow_runs": [run]}
        if path.endswith("/55/jobs?per_page=100"):
            return {"jobs": [{"name": "Reader-only Azure preflight",
                              "conclusion": "success" if job_success else "skipped"}]}
        if path.endswith("/55/artifacts?per_page=100"):
            return {"artifacts": [{"id": 99, "name": "azure-readonly-operations", "expired": False}]}
        if path.endswith("/99/zip") and binary:
            return artifact if artifact is not None else archive()
        raise AssertionError(path)
    return read


class VerifiedHandoffTests(unittest.TestCase):
    def load(self, read):
        return mod.load("ScottMDavistv/azure-text-to-video", "test-token", read=read, now=NOW)

    def test_fresh_protected_main_reader_artifact(self):
        evidence, origin = self.load(fixture())
        self.assertEqual(evidence, INVENTORY)
        self.assertEqual(origin["run_id"], 55)

    def test_invalid_sources_fail_closed(self):
        for overrides in (
            {"head_branch": "feature/unsafe"}, {"conclusion": "failure"},
            {"event": "pull_request"}, {"path": ".github/workflows/other.yml@main"},
            {"repository": {"full_name": "attacker/fork"}},
            {"created_at": "2026-11-15T01:00:00Z"},
            {"created_at": "2026-11-18T01:00:00Z"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(mod.NoVerifiedInventory):
                self.load(fixture(run_overrides=overrides))

    def test_skipped_reader_job_fails_closed(self):
        with self.assertRaises(mod.NoVerifiedInventory):
            self.load(fixture(job_success=False))

    def test_malformed_or_wrong_scope_fails_closed(self):
        for payload in (
            dict(INVENTORY, resource_group="prod"),
            dict(INVENTORY, status="healthy"),
            dict(INVENTORY, resources=[{"type": "Microsoft.Foo/bar"}]),
        ):
            with self.subTest(payload=payload), self.assertRaises(mod.NoVerifiedInventory):
                self.load(fixture(artifact=archive(payload)))

    def test_local_json_is_not_trusted(self):
        source = (ROOT / "scripts/design_management_audit.py").read_text()
        self.assertNotIn('azure_file.exists()', source)
        self.assertIn('verified_azure_inventory import load', source)
        workflow = (ROOT / ".github/workflows/design-management-audit.yml").read_text()
        self.assertNotIn("id-token: write", workflow)
        reader = (ROOT / ".github/workflows/azure-readonly-operations.yml").read_text()
        self.assertIn("python scripts/azure_readonly_inventory.py", reader)
        self.assertIn("azure-inventory.json", reader)


if __name__ == "__main__":
    unittest.main()

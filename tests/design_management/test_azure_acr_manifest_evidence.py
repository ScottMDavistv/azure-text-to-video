"""Offline ACR manifest audit regressions; no Azure, network or secrets."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "azure_acr_manifest_evidence", ROOT / "scripts/azure_acr_manifest_evidence.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
DIGEST = "sha256:" + "a" * 64


class AcrManifestEvidenceTests(unittest.TestCase):
    def test_exact_read_only_cli_and_verified_digest(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps({"digest": DIGEST}))
        self.assertEqual(audit.manifest_metadata(runner=runner), DIGEST)
        self.assertEqual(calls[0][0], [
            "az", "acr", "manifest", "show-metadata",
            "--registry", audit.REGISTRY,
            "--name", audit.REPOSITORY + ":" + audit.EXPECTED_TAG,
            "--output", "json"])
        self.assertFalse(calls[0][1].get("shell", False))
        self.assertEqual(calls[0][1]["timeout"], 45)

    def test_unavailable_acr_fails_closed_without_credentials_or_error_output(self):
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="SECRET-EXAMPLE",
                                   stderr="SECRET-EXAMPLE")
        self.assertIsNone(audit.manifest_metadata(runner=runner))
        self.assertEqual(audit.report_for_digest(None)["state"], "blocked-unverified")
        self.assertNotIn("SECRET-EXAMPLE", json.dumps(audit.report_for_digest(None)))

    def test_timeout_and_missing_az_are_unverified(self):
        for error in (subprocess.TimeoutExpired("az", 45), OSError("SECRET-EXAMPLE")):
            def runner(*args, **kwargs):
                raise error
            self.assertIsNone(audit.manifest_metadata(runner=runner))

    def test_invalid_metadata_fails_closed(self):
        for value in ({"digest": None}, {}, [], {"digest": "sha256:" + "A" * 64},
                      {"digest": "sha256:" + "a" * 63}, {"digest": "not-a-digest"}):
            def runner(*args, **kwargs):
                return SimpleNamespace(returncode=0, stdout=json.dumps(value))
            self.assertIsNone(audit.manifest_metadata(runner=runner))

    def test_metadata_only_never_claims_deployment_authority(self):
        record = audit.report_for_digest(DIGEST)
        self.assertEqual(record["state"], "verified-metadata")
        self.assertTrue(record["no_azure_changes"])
        self.assertEqual(record["manifest_digest"], DIGEST)
        self.assertEqual(record["candidate_immutable_image"],
                         audit.IMAGE + "@" + DIGEST)
        self.assertIn("Independent manifest integrity/provenance and signature",
                      record["not_verified"])

    def test_sanitized_evidence_writes_separate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(audit, "OUTPUT_JSON", pathlib.Path(tmp) / "report.json"), \
                 patch.object(audit, "OUTPUT_MD", pathlib.Path(tmp) / "report.md"):
                audit.write_report(audit.report_for_digest(None))
                result = json.loads(audit.OUTPUT_JSON.read_text())
                self.assertIsNone(result["manifest_digest"])
                self.assertIn("NOT VERIFIED", audit.OUTPUT_MD.read_text())

    def test_workflow_is_main_only_and_does_not_write_or_get_credentials(self):
        workflow = (ROOT / ".github/workflows/azure-readonly-operations.yml").read_text()
        self.assertIn("environment: azure-readonly-audit", workflow)
        self.assertIn("AZURE_AUDIT_OPERATIONS_ENABLED", workflow)
        self.assertIn("python scripts/azure_acr_manifest_evidence.py", workflow)
        self.assertIn("azure-acr-manifest-evidence.json", workflow)
        self.assertNotIn("az acr login", workflow)
        self.assertNotIn("az acr import", workflow)
        self.assertNotIn("az role assignment create", workflow)

    def test_main_and_activation_guards(self):
        with patch.dict(audit.os.environ, {"GITHUB_REF": "refs/heads/feature",
                                        "AZURE_AUDIT_OPERATIONS_ENABLED": "true"}):
            with self.assertRaises(SystemExit):
                audit.main()
        with patch.dict(audit.os.environ, {"GITHUB_REF": "refs/heads/main",
                                        "AZURE_AUDIT_OPERATIONS_ENABLED": "false"}):
            with self.assertRaises(SystemExit):
                audit.main()


if __name__ == "__main__":
    unittest.main()

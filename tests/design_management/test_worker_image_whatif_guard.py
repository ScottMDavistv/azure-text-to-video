"""Offline negative-path tests of guarded Azure what-if approval boundary."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "guarded_whatif", ROOT / "scripts/verify_worker_image_whatif.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def good():
    return {"changes": [{
        "resourceId": guard.RESOURCE_ID,
        "changeType": "Modify",
        "delta": [{
            "path": guard.PATH,
            "propertyChangeType": "Modify",
            "before": guard.OLD_IMAGE,
            "after": guard.NEW_IMAGE,
        }],
    }]}


class ImageOnlyWhatifTests(unittest.TestCase):
    def test_exact_reviewed_change_passes(self):
        self.assertTrue(guard.verify(good()))

    def test_extra_changes_and_ambiguous_payloads_fail_closed(self):
        extra = json.loads(json.dumps(good()))
        extra["changes"].append({"changeType": "Delete", "resourceId": guard.RESOURCE_ID})
        cases = [
            {}, {"changes": []}, {"changes": None}, {"changes": "image"},
            extra, {"changes": [None]},
            {"changes": [dict(good()["changes"][0], changeType="Create")]},
            {"changes": [dict(good()["changes"][0], changeType="NoChange")]},
            {"changes": [dict(good()["changes"][0], changeType="Ignore")]},
            {"changes": [dict(good()["changes"][0], resourceId=guard.RESOURCE_ID + "-other")]},
            {"changes": [dict(good()["changes"][0], delta=[])]},
            {"changes": [dict(good()["changes"][0], delta=[good()["changes"][0]["delta"][0]] * 2)]},
            {"changes": [dict(good()["changes"][0], unrelatedChanges=True)]},
            dict(good(), dangerous=True),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(guard.PreviewRejected):
                    guard.verify(candidate)

    def test_non_image_and_wrong_image_fail_closed(self):
        for key, value in (
            ("path", "properties.configuration.triggerType"),
            ("path", "properties.template.containers[1].image"),
            ("propertyChangeType", "Create"),
            ("before", "unknown-image"),
            ("after", guard.OLD_IMAGE),
            ("after", "attacker/image:latest"),
        ):
            candidate = good()
            candidate["changes"][0]["delta"][0][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(guard.PreviewRejected):
                    guard.verify(candidate)

    def test_failures_never_emit_raw_azure_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            path.write_text('{"secret":"SHOULD-NOT-APPEAR"}')
            with patch("sys.stderr") as stderr:
                self.assertEqual(guard.main([str(path)]), 2)
                output = "".join(str(c) for c in stderr.write.call_args_list)
                self.assertNotIn("SHOULD-NOT-APPEAR", output)
            path.write_text(json.dumps(good()))
            self.assertEqual(guard.main([str(path)]), 0)
            path.write_text("NOT JSON")
            self.assertEqual(guard.main([str(path)]), 2)

    def test_preview_only_route_keeps_private_image_guard_without_apply(self):
        caller = (ROOT / ".github/workflows/guarded-azure-worker-deploy.yml").read_text()
        callee = (ROOT / ".github/workflows/dev-worker-whatif-preview.yml").read_text()
        for block in (caller, callee):
            self.assertNotIn("az deployment group create", block)
            self.assertNotIn("\n  deploy:", block)
            self.assertIn("if: ${{ false }}", block)
            self.assertNotIn("id-token: write", block)
        self.assertIn("--result-format FullResourcePayloads --output json", callee)
        self.assertIn("python scripts/verify_worker_image_whatif.py", callee)
        self.assertIn("umask 077", callee)
        self.assertIn("trap 'rm -f \"$preview_file\"' EXIT", callee)
        self.assertIn("environment: dev-worker-whatif-preview", callee)
        self.assertIn("--validation-level ProviderNoRbac", callee)


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the untrusted-PR/privileged-workflow boundary."""
import copy
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("isolation", ROOT / "scripts/check_ci_isolation.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
BASE = {
    "on": {"pull_request": None},
    "permissions": {"contents": "read"},
    "jobs": {"checks": {"runs-on": "ubuntu-latest", "steps": [
        {"uses": "actions/checkout@v4", "with": {"persist-credentials": "false"}}
    ]}}
}

class IsolationTests(unittest.TestCase):
    def evaluate(self, document, raw=""):
        return checker.examine(".github/workflows/test.yml", document, raw)

    def test_readonly_pull_request_accepted(self):
        self.assertEqual(self.evaluate(BASE), [])

    def test_write_permission_rejected(self):
        obj = copy.deepcopy(BASE)
        obj["permissions"]["contents"] = "write"
        self.assertTrue(self.evaluate(obj))

    def test_secret_reference_rejected(self):
        secret_expression = "$" + "{{ secrets.COPILOT_DISPATCH_TOKEN }}"
        self.assertTrue(self.evaluate(BASE, "env: " + secret_expression))

    def test_checkout_without_persist_false_rejected(self):
        obj = copy.deepcopy(BASE)
        obj["jobs"]["checks"]["steps"][0]["with"] = {}
        self.assertTrue(self.evaluate(obj))

    def test_pull_request_target_denied_except_gate(self):
        obj = copy.deepcopy(BASE)
        obj["on"] = {"pull_request_target": None}
        self.assertTrue(self.evaluate(obj))

    def test_ungated_pr_oidc_rejected(self):
        obj = copy.deepcopy(BASE)
        obj["jobs"]["checks"]["permissions"] = {"contents": "read", "id-token": "write"}
        self.assertTrue(self.evaluate(obj))
        obj["jobs"]["checks"]["if"] = ("github.ref == 'refs/heads/main' && "
                                        "github.event_name != 'pull_request'")
        self.assertEqual(self.evaluate(obj), [])

    def test_privileged_manual_run_must_be_main_only(self):
        obj = {"on": {"workflow_dispatch": None},
               "permissions": {"contents": "read", "id-token": "write"},
               "jobs": {"preview": {"runs-on": "ubuntu-latest", "steps": []}}}
        self.assertTrue(self.evaluate(obj))
        obj["jobs"]["preview"]["if"] = "github.ref == 'refs/heads/main'"
        self.assertEqual(self.evaluate(obj), [])

    def test_real_workflow_tree_when_yaml_installed(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pinned PyYAML provided by policy gate; normal backend CI uses mock tests")
        self.assertEqual(checker.scan(ROOT), [])

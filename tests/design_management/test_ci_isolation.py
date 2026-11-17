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


    def test_custom_secret_requires_environment_and_main(self):
        token_ref = "$" + "{{ secrets.DM_COPILOT_DISPATCH_TOKEN }}"
        obj = {"on": {"workflow_dispatch": None},
               "permissions": {"contents": "read"},
               "jobs": {"controller": {"runs-on": "ubuntu-latest",
                                       "env": {"TOKEN": token_ref},
                                       "steps": []}}}
        self.assertTrue(self.evaluate(obj))
        obj["jobs"]["controller"]["environment"] = {"name": "design-management-control",
                                                         "deployment": "false"}
        self.assertTrue(self.evaluate(obj))
        obj["jobs"]["controller"]["if"] = "github.ref == 'refs/heads/main'"
        self.assertEqual(self.evaluate(obj), [])

    def test_legacy_controller_secret_names_are_forbidden(self):
        job = {"runs-on": "ubuntu-latest",
               "if": "github.ref == 'refs/heads/main'",
               "environment": "design-management-control",
               "env": {"TOKEN": "$" + "{{ secrets.COPILOT_DISPATCH_TOKEN }}"},
               "steps": []}
        document = {"on": {"workflow_dispatch": None},
                    "permissions": {"contents": "read"},
                    "jobs": {"controller": job}}
        findings = checker.examine(".github/workflows/autonomous-engineering.yml",
                                   document)
        self.assertTrue(any("legacy repository secret" in problem for problem in findings))

    def test_controller_uses_environment_only_credentials(self):
        workflow = (ROOT / ".github/workflows/autonomous-engineering.yml").read_text()
        self.assertIn("name: design-management-control", workflow)
        self.assertIn("vars.CONTROLLER_ENV_ISOLATION_VERIFIED == 'true'", workflow)
        self.assertIn("secrets.DM_COPILOT_DISPATCH_TOKEN", workflow)
        self.assertIn("secrets.DM_AUTO_MERGE_USER_TOKEN", workflow)
        self.assertNotIn("secrets.COPILOT_DISPATCH_TOKEN", workflow)
        self.assertNotIn("secrets.AUTO_MERGE_USER_TOKEN", workflow)

    def test_legacy_stage_b_uses_no_repository_secret_references(self):
        workflow = (ROOT / ".github/workflows/stage-b-audit.yml").read_text()
        for item in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"):
            self.assertIn("vars." + item, workflow)
            self.assertNotIn("secrets." + item, workflow)

    def test_real_workflow_tree_when_yaml_installed(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pinned PyYAML provided by policy gate; normal backend CI uses mock tests")
        self.assertEqual(checker.scan(ROOT), [])

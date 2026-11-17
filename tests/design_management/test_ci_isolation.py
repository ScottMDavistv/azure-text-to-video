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


    def test_or_and_negated_main_conditions_fail_closed(self):
        bad = [
            "github.ref != 'refs/heads/main' && github.event_name != 'pull_request'",
            "github.ref != 'refs/heads/main' && github.event_name != 'pull_request' || true",
            "github.ref == 'refs/heads/main' || true",
            "!(github.ref == 'refs/heads/main')",
            "github.ref == 'refs/heads/main' && always()",
        ]
        for expr in bad:
            self.assertFalse(checker.main_only(expr))
            obj = copy.deepcopy(BASE)
            obj["jobs"]["checks"]["permissions"] = {"id-token": "write"}
            obj["jobs"]["checks"]["if"] = expr
            self.assertTrue(self.evaluate(obj), expr)

    def test_untrusted_feature_push_rejects_write_secrets_and_checkout_credentials(self):
        obj = copy.deepcopy(BASE)
        obj["on"] = {"push": {"branches": ["feature/**"]}}
        obj["permissions"] = {"contents": "write"}
        self.assertTrue(self.evaluate(obj))
        obj["permissions"] = {"contents": "read"}
        obj["jobs"]["checks"]["steps"][0]["with"] = {}
        self.assertTrue(self.evaluate(obj))
        obj["jobs"]["checks"]["steps"][0]["with"] = {"persist-credentials": "false"}
        obj["jobs"]["checks"]["env"] = {"TOKEN": "$" + "{{ secrets['EXAMPLE_SECRET'] }}"}
        self.assertTrue(self.evaluate(obj))
        obj["on"] = {"push": {"branches": ["main"]}}
        self.assertFalse(checker.unsafe_push(obj))

    def test_secret_bracket_and_nested_expression_access(self):
        for expression in (
            "$" + "{{ secrets['EXAMPLE_SECRET'] }}",
            "$" + '{{ secrets["EXAMPLE_SECRET"] }}',
            "$" + "{{ toJSON(secrets) }}",
            "$" + "{{ fromJSON(toJSON(secrets))['KEY'] }}",
        ):
            obj = copy.deepcopy(BASE)
            obj["env"] = {"LEAK": expression}
            self.assertTrue(self.evaluate(obj), expression)

    def test_trusted_gate_exact_structural_allowlist(self):
        name = checker.GATE
        correct = checker.expected_gate()
        self.assertEqual(checker.examine(name, correct), [])
        for mutate in (
            lambda o: o["jobs"]["audit"]["steps"][0]["with"].update(
                {"ref": "$" + "{{ github.event.pull_request.head.sha }}"}),
            lambda o: o["jobs"]["audit"]["steps"][1]["with"].update(
                {"path": "trusted"}),
            lambda o: o["jobs"]["audit"]["steps"][4].update(
                {"run": "python candidate/scripts/check_ci_isolation.py --root candidate"}),
            lambda o: o["jobs"]["audit"]["steps"].append(
                {"run": "python candidate/test.py"}),
            lambda o: o["jobs"]["audit"]["steps"][0].update(
                {"working-directory": "candidate"}),
            lambda o: o["jobs"]["audit"]["steps"][2].update(
                {"uses": "actions/setup-python@v5"}),
        ):
            obj = copy.deepcopy(correct)
            mutate(obj)
            self.assertTrue(checker.examine(name, obj))

    def test_untrusted_push_job_readonly_only_even_with_main_guard(self):
        obj = copy.deepcopy(BASE)
        obj["on"] = {"push": {"branches": ["feature/**", "main"]}}
        obj["jobs"]["checks"]["if"] = "github.ref == 'refs/heads/main'"
        obj["jobs"]["checks"]["permissions"] = {"contents": "write"}
        self.assertTrue(self.evaluate(obj))

    def test_scalar_and_list_push_triggers_are_untrusted(self):
        for trigger in ("push", ["push"], ["workflow_dispatch", "push"]):
            obj = copy.deepcopy(BASE)
            obj["on"] = trigger
            obj["permissions"] = {"contents": "write"}
            self.assertTrue(checker.unsafe_push(obj), str(trigger))
            self.assertTrue(self.evaluate(obj), str(trigger))
            obj["permissions"] = {"contents": "read"}
            obj["jobs"]["checks"]["steps"][0]["with"] = {}
            self.assertTrue(self.evaluate(obj), str(trigger))

    def test_inherited_workflow_env_secret_requires_main_and_environment(self):
        obj = {"on": {"workflow_dispatch": None},
               "permissions": {"contents": "read"},
               "env": {"CREDENTIAL": "$" + "{{ secrets.DM_COPILOT_DISPATCH_TOKEN }}"},
               "jobs": {"check": {"runs-on": "ubuntu-latest", "steps": []}}}
        findings = self.evaluate(obj)
        self.assertTrue(any("custom secret requires" in item for item in findings))
        obj["jobs"]["check"]["environment"] = "design-management-control"
        self.assertTrue(any("guarded on main" in item for item in self.evaluate(obj)))
        obj["jobs"]["check"]["if"] = "github.ref == 'refs/heads/main'"
        self.assertEqual(self.evaluate(obj), [])

    def test_yaml_extension_included_in_scan(self):
        import tempfile
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML unavailable")
        with tempfile.TemporaryDirectory() as root:
            folder = pathlib.Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "unsafe.yaml").write_text(
                "on: push\\npermissions:\\n  contents: write\\njobs:\\n"
                "  bad:\\n    runs-on: ubuntu-latest\\n    steps: []\\n"
            )
            self.assertTrue(checker.scan(root))

    def test_real_workflow_tree_when_yaml_installed(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pinned PyYAML provided by policy gate; normal backend CI uses mock tests")
        self.assertEqual(checker.scan(ROOT), [])

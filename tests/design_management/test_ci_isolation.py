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


E = "$" + "{{ "  # avoid literal expressions in this test file


def yaml_scan(test, files):
    import tempfile
    try:
        import yaml  # noqa: F401
    except ImportError:
        test.skipTest("PyYAML unavailable")
    with tempfile.TemporaryDirectory() as root:
        folder = pathlib.Path(root) / ".github/workflows"
        folder.mkdir(parents=True)
        for name, text in files.items():
            (folder / name).write_text(text)
        return checker.scan(root)


class AdversarialIsolationTests(unittest.TestCase):
    """Negative-path regressions from the Nov 17 independent review of PR #50."""

    def evaluate(self, document):
        return checker.examine(".github/workflows/test.yml", document)

    def job(self, **extra):
        job = {"runs-on": "ubuntu-latest", "steps": []}
        job.update(extra)
        return job

    # 1. Malicious fork PR attempting credential access
    def test_fork_pr_credential_access_variants_rejected(self):
        for env in ({"T": E + "secrets.AZURE_CLIENT_SECRET }}"},
                    {"T": E + "toJSON(secrets) }}"},
                    {"T": E + "secrets[format('{0}', 'AZ')] }}"}):
            obj = {"on": {"pull_request": ""}, "permissions": {"contents": "read"},
                   "jobs": {"j": self.job(env=env)}}
            self.assertTrue(self.evaluate(obj), env)
        obj = {"on": "pull_request", "permissions": {"contents": "read"},
               "jobs": {"j": self.job(steps=[{"uses": "actions/checkout@v4"}])}}
        self.assertTrue(any("persists credentials" in p for p in self.evaluate(obj)))

    # 2. Untrusted workflow attempting privilege escalation
    def test_workflow_run_cannot_execute_pr_head_with_privilege(self):
        obj = {"on": {"workflow_run": {"workflows": ["CI"], "types": ["completed"]}},
               "permissions": {"contents": "read"},
               "jobs": {"j": self.job(**{
                   "if": "github.ref == 'refs/heads/main'", "environment": "prod",
                   "steps": [{"uses": "actions/checkout@v4", "with": {
                       "ref": E + "github.event.workflow_run.head_sha }}",
                       "persist-credentials": "false"}}]})}}
        self.assertTrue(any("untrusted ref" in p for p in self.evaluate(obj)))

    def test_event_text_script_injection_rejected(self):
        for field in ("github.event.issue.title", "github.event.pull_request.body",
                      "github.head_ref", "github.event.workflow_run.head_branch",
                      "toJSON(github)", "github.event['comment']"):
            obj = {"on": {"issues": ""}, "permissions": {"contents": "read"},
                   "jobs": {"j": self.job(steps=[{"run": "echo " + E + field + " }}"}])}}
            self.assertTrue(any("untrusted event text" in p for p in self.evaluate(obj)), field)
        safe = {"on": {"pull_request": ""}, "permissions": {"contents": "read"},
                "jobs": {"j": self.job(steps=[{"run": "git diff " + E +
                                               "github.event.pull_request.base.sha }}"}])}}
        self.assertEqual(self.evaluate(safe), [])

    def test_unsupported_triggers_denied(self):
        for trigger in ("issue_comment", "pull_request_review", "pull_request_review_comment",
                        "merge_group", "discussion", "check_run", "repository_dispatch"):
            obj = {"on": [trigger], "permissions": {"contents": "read"},
                   "jobs": {"j": self.job()}}
            self.assertTrue(any("unsupported trigger" in p for p in self.evaluate(obj)), trigger)

    def test_duplicate_merge_and_alias_yaml_denied(self):
        dup = ("on: pull_request\npermissions: write-all\npermissions: {contents: read}\n"
               "jobs:\n  j: {runs-on: ubuntu-latest, steps: []}\n")
        merge = ("on: pull_request\npermissions: {contents: read}\nx: &p {permissions: write-all}\n"
                 "jobs:\n  j: {<<: *p, runs-on: ubuntu-latest, steps: []}\n")
        recursive = "on: pull_request\npermissions: {contents: read}\njobs: &a {j: *a}\n"
        for text in (dup, merge, recursive):
            self.assertTrue(yaml_scan(self, {"x.yml": text}), text)

    # 3. Unauthorized workflow dispatch
    def test_dispatch_privileged_job_needs_positive_main_guard(self):
        for cond in ("", "github.ref != 'refs/heads/main'",
                     "github.ref == 'refs/heads/main' || true",
                     "!(github.ref != 'refs/heads/main')",
                     "contains(github.ref, 'main')"):
            obj = {"on": {"workflow_dispatch": ""},
                   "permissions": {"contents": "read", "id-token": "write"},
                   "jobs": {"j": self.job(**({"if": cond} if cond else {}))}}
            self.assertTrue(any("guarded on main" in p for p in self.evaluate(obj)), cond)

    # 4. Reusable workflow permission inheritance
    def test_reusable_workflow_secret_inheritance_rejected(self):
        caller = {"on": {"pull_request": ""}, "permissions": {"contents": "read"},
                  "jobs": {"c": {"uses": "./.github/workflows/deploy.yml", "secrets": "inherit"}}}
        self.assertTrue(any("inherit" in p for p in self.evaluate(caller)))
        caller["jobs"]["c"]["secrets"] = {"AZ": E + "secrets.AZ }}"}
        self.assertTrue(self.evaluate(caller))
        remote = {"on": {"workflow_dispatch": ""}, "permissions": {"contents": "read"},
                  "jobs": {"c": {"if": "github.ref == 'refs/heads/main'",
                                 "uses": "evil/repo/.github/workflows/x.yml@main"}}}
        self.assertTrue(any("pinned" in p for p in self.evaluate(remote)))

    def test_workflow_call_callee_treated_as_caller_context(self):
        callee = {"on": {"workflow_call": ""}, "permissions": {"contents": "read"},
                  "jobs": {"d": self.job(environment="prod",
                                         steps=[{"run": "deploy", "env": {
                                             "T": E + "secrets.AZ }}"}}])}}
        findings = self.evaluate(callee)
        self.assertTrue(any("guarded on main" in p for p in findings))
        callee["jobs"]["d"]["permissions"] = {"contents": "write"}
        self.assertTrue(any("write/OIDC" in p for p in self.evaluate(callee)))

    # 5. Protected environment approval bypass
    def test_environment_unreachable_from_untrusted_jobs(self):
        for env in ("dev-deployment", {"name": E + "github.event.pull_request.head.ref }}"}):
            obj = {"on": {"pull_request": ""}, "permissions": {"contents": "read"},
                   "jobs": {"j": self.job(environment=env)}}
            self.assertTrue(any("environment" in p for p in self.evaluate(obj)), env)

    # 6. Changed PR head after approval
    def test_gate_binds_to_exact_event_head_sha_and_restarts_on_push(self):
        gate = checker.expected_gate()
        candidate = gate["jobs"]["audit"]["steps"][1]["with"]
        self.assertEqual(candidate["ref"], "$" + "{{ github.event.pull_request.head.sha }}")
        self.assertIn("synchronize", gate["on"]["pull_request_target"]["types"])
        self.assertEqual(gate["concurrency"]["cancel-in-progress"], "true")
        for ref in ("$" + "{{ github.event.pull_request.head.ref }}", "$" + "{{ github.head_ref }}"):
            mutated = copy.deepcopy(gate)
            mutated["jobs"]["audit"]["steps"][1]["with"]["ref"] = ref
            self.assertTrue(checker.examine(checker.GATE, mutated), ref)

    def test_gate_dependency_is_hash_pinned(self):
        install = checker.expected_gate()["jobs"]["audit"]["steps"][3]["run"]
        self.assertIn("--require-hashes", install)
        reqs = (ROOT / "scripts/ci-isolation-requirements.txt").read_text()
        self.assertEqual(reqs.count("--hash=sha256:"), 2)
        self.assertIn("PyYAML==6.0.2", reqs)

    # 7. Attempts to bypass branch protection via push triggers
    def test_push_branch_filters_other_than_exact_main_are_untrusted(self):
        for push in ({"branches": ["**"]}, {"branches": ["main", "feature/**"]},
                     {"branches-ignore": ["main"]}, {"branches": ["main"], "tags": ["v*"]},
                     {"branches": ["ma*n"]}, "", None):
            obj = {"on": {"push": push}, "permissions": {"contents": "write"},
                   "jobs": {"j": self.job(**{"if": "github.ref == 'refs/heads/main'"})}}
            self.assertTrue(self.evaluate(obj), push)

    # 8. Failure or unavailability of the checks themselves
    def test_scanner_fails_closed_when_unavailable_or_unreadable(self):
        import io
        import sys
        from unittest import mock
        with mock.patch.dict(sys.modules, {"yaml": None}), \
                mock.patch.object(sys, "argv", ["x", "--root", str(ROOT)]), \
                mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                checker.main()
        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue(yaml_scan(self, {}))  # no workflows → deny
        self.assertTrue(yaml_scan(self, {"x.yml": "a: [unclosed\n"}))
        self.assertTrue(yaml_scan(self, {"x.yml": "---\non: push\n---\non: push\n"}))
        self.assertTrue(yaml_scan(self, {"x.YML": "on: push\npermissions: write-all\n"
                                                  "jobs:\n  j: {runs-on: x, steps: []}\n"}))

    def test_symlinked_workflow_denied(self):
        import os
        import tempfile
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML unavailable")
        with tempfile.TemporaryDirectory() as root:
            folder = pathlib.Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            os.symlink("/etc/hostname", folder / "x.yml")
            self.assertTrue(any("regular file" in p for p in checker.scan(root)))

"""No Azure access required: fail-closed manual what-if workflow regressions."""
import importlib.util
import contextlib
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/azure_worker_whatif.py"
spec = importlib.util.spec_from_file_location("worker_whatif", SCRIPT)
whatif = importlib.util.module_from_spec(spec)
spec.loader.exec_module(whatif)

SAFE_TEMPLATE = """
@allowed([
  'acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646'
])
param workerImage string
resource worker 'Microsoft.App/jobs@2024-03-01' = {
  properties: {
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaRetryLimit: 0
      secrets: []
    }
    template: {
      containers: [
        {
          command: ['python']
          args: ['worker_runtime.py', '--check']
          env: [
            { name: 'VIDEO_RENDER_PROVIDER', value: 'disabled' }
            { name: 'ENABLE_WORKER_CONSUMPTION', value: 'false' }
            { name: 'ENABLE_LIVE_VIDEO_RENDERING', value: 'false' }
          ]
        }
      ]
    }
  }
}
"""
ENV = {
    "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
    "AZURE_AUDIT_OPERATIONS_ENABLED": "true",
    "GITHUB_REPOSITORY": "ScottMDavistv/azure-text-to-video",
    "GITHUB_REPOSITORY_ID": "1457187422",
    "GITHUB_REPOSITORY_OWNER_ID": "212811615",
    "AZURE_SUBSCRIPTION_ID": whatif.SUBSCRIPTION,
}


def job():
    return {
        "id": whatif.JOB_ID, "identity": {
            "type": "UserAssigned", "userAssignedIdentities": {"worker-identity": {}}},
        "properties": {
            "configuration": {"triggerType": "Manual", "secrets": []},
            "workloadProfileName": "Consumption",
            "template": {"containers": [{
                "image": whatif.EXISTING_IMAGE,
                "command": ["python"], "args": ["worker_runtime.py", "--check"],
                "env": [
                    {"name": "VIDEO_RENDER_PROVIDER", "value": "disabled"},
                    {"name": "ENABLE_WORKER_CONSUMPTION", "value": "false"},
                    {"name": "ENABLE_LIVE_VIDEO_RENDERING", "value": "false"},
                ],
            }]},
        },
    }


def preview(change=None):
    return {"changes": [change or {
        "resourceId": whatif.JOB_ID, "changeType": "Modify", "delta": [{
            "path": "properties.template.containers[0].image",
            "propertyChangeType": "Modify",
            "before": whatif.EXISTING_IMAGE, "after": whatif.IMAGE,
        }],
    }]}


class WorkerWhatIfTests(unittest.TestCase):
    def test_safe_reviewed_template_and_environment(self):
        whatif.validate_template(SAFE_TEMPLATE)
        whatif.preflight_env(ENV)

    def test_wrong_branch_trigger_repo_identity_and_optin_block(self):
        for key, bad in (
            ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("AZURE_AUDIT_OPERATIONS_ENABLED", "false"),
            ("GITHUB_REPOSITORY", "attacker/repo"),
            ("GITHUB_REPOSITORY_ID", "000"),
            ("GITHUB_REPOSITORY_OWNER_ID", "000"),
            ("AZURE_SUBSCRIPTION_ID", "000"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(whatif.Blocked):
                    whatif.preflight_env({**ENV, key: bad})

    def test_other_managed_resources_or_unsafe_template_block(self):
        for unsafe in (
            SAFE_TEMPLATE.replace("Manual", "Event"),
            SAFE_TEMPLATE.replace("disabled", "fal"),
            SAFE_TEMPLATE + "\nresource bad 'Microsoft.Authorization/roleAssignments@2022-04-01' = {}",
            SAFE_TEMPLATE.replace("secrets: []", "secrets: [{ name: 'fal' }]"),
            SAFE_TEMPLATE.replace("--check", "--run"),
            SAFE_TEMPLATE.replace(whatif.IMAGE, whatif.EXISTING_IMAGE),
        ):
            with self.assertRaises(whatif.Blocked):
                whatif.validate_template(unsafe)

    def test_existing_worker_config_baseline(self):
        self.assertTrue(whatif.check_existing(job()))
        bad = job()
        bad["properties"]["template"]["containers"][0]["image"] = whatif.IMAGE
        with self.assertRaises(whatif.Blocked):
            whatif.check_existing(bad)
        bad = job()
        bad["properties"]["template"]["containers"][0]["env"][0]["value"] = "fal"
        with self.assertRaises(whatif.Blocked):
            whatif.check_existing(bad)

    def test_exact_image_only_diff_is_accepted(self):
        result = whatif.classify_whatif(preview())
        self.assertTrue(result["image_only"])
        self.assertEqual(result["new_image_digest"], whatif.DIGEST)

    def test_unexpected_changes_fail_closed(self):
        cases = [
            {}, {"changes": []},
            {"changes": [{"resourceId": whatif.JOB_ID, "changeType": "Create"}]},
            {"changes": [preview()["changes"][0], preview()["changes"][0]]},
            preview({"resourceId": whatif.JOB_ID + "-wrong",
                     "changeType": "Modify", "delta": []}),
            preview({"resourceId": whatif.JOB_ID, "changeType": "Modify",
                     "delta": [{"path": "properties.configuration.secrets[0]",
                                "propertyChangeType": "Modify",
                                "before": None, "after": "secret"}]}),
            preview({"resourceId": whatif.JOB_ID, "changeType": "Modify",
                     "delta": [{"path": "properties.template.containers[0].image",
                                "propertyChangeType": "Modify",
                                "before": whatif.EXISTING_IMAGE, "after": "malicious"}]}),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(whatif.Blocked):
                    whatif.classify_whatif(case)

    def test_only_approved_azure_cli_operations(self):
        commands = []
        responses = [
            {"id": whatif.SUBSCRIPTION},
            job(),
            preview(),
        ]
        def runner(argv, **kwargs):
            commands.append(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)))
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "worker-job.bicep"
            path.write_text(SAFE_TEMPLATE)
            result = whatif.execute(runner=runner, environment=ENV, template_path=path)
        self.assertTrue(result["image_only"])
        self.assertEqual(commands[0], ["az", "account", "show", "--output", "json"])
        self.assertEqual(commands[1][0:3], ["az", "resource", "show"])
        self.assertEqual(commands[2][0:4], ["az", "deployment", "group", "what-if"])
        self.assertEqual(commands[2][-2:], ["--output", "json"])
        self.assertTrue(all(not any(token in cmd for token in (
            "create", "apply", "start", "role", "import", "login")) for cmd in commands))

    def test_denied_whatif_never_leaks_raw_cli_response(self):
        secret = "SHOULD-NOT-APPEAR"
        def runner(argv, **kwargs):
            return SimpleNamespace(returncode=1, stdout=secret, stderr=secret)
        with self.assertRaises(whatif.Blocked) as exc:
            whatif.run_json(["az", "deployment", "group", "what-if"], runner=runner)
        self.assertNotIn(secret, str(exc.exception))

    def test_reports_sanitized_and_do_not_claim_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(whatif, "EVIDENCE", pathlib.Path(tmp) / "data.json"), \
                 patch.object(whatif, "SUMMARY", pathlib.Path(tmp) / "data.md"):
                whatif.write_report(whatif.report(blocked="not permitted"))
                data = json.loads(whatif.EVIDENCE.read_text())
                self.assertEqual(data["state"], "blocked-unverified")
                self.assertFalse(data["deployment_performed"])
                self.assertFalse(data["iam_modified"])

    def test_failure_stage_is_fixed_and_no_raw_azure_error_escapes(self):
        secret = "ACCESS-TOKEN-OR-SENSITIVE-ARM-DATA"
        cases = [
            (0, "account-inspection"),
            (1, "worker-resource-read"),
            (2, "arm-whatif-preview"),
        ]
        for fail_index, stage in cases:
            with self.subTest(stage=stage):
                responses = [
                    {"id": whatif.SUBSCRIPTION},
                    job(),
                    preview(),
                ]
                calls = []
                def runner(argv, **kwargs):
                    index = len(calls)
                    calls.append(argv)
                    if index == fail_index:
                        return SimpleNamespace(returncode=1, stdout=secret, stderr=secret)
                    return SimpleNamespace(returncode=0, stdout=json.dumps(responses[index]), stderr="")
                with tempfile.TemporaryDirectory() as tmp:
                    source = pathlib.Path(tmp) / "worker-job.bicep"
                    source.write_text(SAFE_TEMPLATE)
                    with self.assertRaises(whatif.Blocked) as failure:
                        whatif.execute(runner=runner, environment=ENV, template_path=source)
                self.assertEqual(failure.exception.stage, stage)
                sanitized = whatif.report(blocked=str(failure.exception),
                                          stage=failure.exception.stage)
                self.assertEqual(sanitized["diagnostic_stage"], stage)
                self.assertEqual(sanitized["state"], "blocked-unverified")
                self.assertNotIn(secret, json.dumps(sanitized))
                self.assertFalse(sanitized["deployment_performed"])
                self.assertEqual(len(calls), fail_index + 1)

    def test_unexpected_diff_is_classification_stage_only(self):
        responses = [
            {"id": whatif.SUBSCRIPTION},
            job(),
            {"changes": [{"resourceId": whatif.JOB_ID,
                          "changeType": "Delete"}]},
        ]
        def runner(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "worker-job.bicep"
            source.write_text(SAFE_TEMPLATE)
            with self.assertRaises(whatif.Blocked) as failure:
                whatif.execute(runner=runner, environment=ENV, template_path=source)
        self.assertEqual(failure.exception.stage, "image-only-diff-classification")

    def test_unapproved_diagnostic_stage_fails_closed(self):
        with self.assertRaises(whatif.Blocked):
            whatif.run_json(["az", "account", "show"], stage="../sensitive-data")
        with self.assertRaises(ValueError):
            whatif.report(blocked="unknown", stage="raw-stderr")

    def test_diagnostic_report_artifact_contains_only_stage_and_fixed_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(whatif, "EVIDENCE", pathlib.Path(tmp) / "data.json"), \
                 patch.object(whatif, "SUMMARY", pathlib.Path(tmp) / "data.md"):
                whatif.write_report(whatif.report(
                    blocked="Azure operation unavailable or not permitted by current identity",
                    stage="worker-resource-read"))
                report = json.loads(whatif.EVIDENCE.read_text())
                self.assertEqual(report["diagnostic_stage"], "worker-resource-read")
                self.assertIn("worker-resource-read", whatif.SUMMARY.read_text())
                self.assertNotIn("stderr", whatif.SUMMARY.read_text().lower())

    def test_only_structured_azure_arm_codes_are_classified(self):
        expected = {
            "ERROR: (AuthorizationFailed) Sensitive details": "rbac-denied",
            "(LinkedAuthorizationFailed) scope hidden": "rbac-denied",
            '{"error":{"code":"AuthorizationFailed","message":"secret"}}': "rbac-denied",
            "ERROR: (InvalidTemplate) details": "template-validation",
            "(InvalidTemplateDeployment) details": "template-validation",
            '{"error":{"code":"InvalidDeploymentParameter","message":"secret"}}': "template-validation",
            "(BicepError) compilation": "template-validation",
            "ERROR: (SomeOtherError) AuthorizationFailed in details": "unknown",
            "AuthorizationFailed in body of message": "unknown",
            '{"message":"(AuthorizationFailed) narrative only"}': "unknown",
            '{"error":{"message":"AuthorizationFailed"}}': "unknown",
            '{"error":{"code":"AuthorizationFailedExploit"}}': "unknown",
            "(AuthorizationFailedExtra) content": "unknown",
            "": "unknown",
        }
        for stderr, category in expected.items():
            with self.subTest(stderr=stderr[:50]):
                self.assertEqual(whatif.classify_azure_failure(stderr), category)
        self.assertEqual(whatif.classify_azure_failure(None), "unknown")
        self.assertEqual(whatif.classify_azure_failure("X" * 65537), "unknown")

    def test_conflicting_multiline_cli_codes_fail_closed(self):
        samples = (
            "ERROR: (AuthorizationFailed) first\nERROR: (InvalidTemplate) second",
            "ERROR: (InvalidTemplate) first\n(AuthorizationFailed) second",
            "(AuthorizationFailed) first\n(AuthorizationFailed) duplicate",
            "ERROR: (AuthorizationFailed) first\n  ERROR: (LinkedAuthorizationFailed) second",
            "ERROR: (AuthorizationFailed) first\n(UnknownFutureCode) second",
        )
        for stderr in samples:
            with self.subTest(stderr=stderr):
                self.assertEqual(whatif.classify_azure_failure(stderr), "unknown")
                def runner(argv, **kwargs):
                    return SimpleNamespace(returncode=1, stdout="PRIVATE-OUTPUT", stderr=stderr)
                with self.assertRaises(whatif.Blocked) as failure:
                    whatif.run_json(["az", "deployment", "group", "what-if"],
                                    runner=runner, stage="arm-whatif-preview")
                self.assertEqual(failure.exception.error_category, "unknown")
                data = whatif.report(blocked=str(failure.exception),
                                     stage=failure.exception.stage,
                                     error_category=failure.exception.error_category)
                self.assertEqual(data["arm_error_category"], "unknown")
                self.assertNotIn("PRIVATE-OUTPUT", json.dumps(data))
                self.assertNotIn("AuthorizationFailed", json.dumps(data))

    def test_narrative_codes_and_malformed_envelopes_never_infer_rbac(self):
        samples = (
            "AuthorizationFailed mentioned in prose",
            "ERROR: (InvalidTemplate) detail mentions AuthorizationFailed",
            '{"error":{"code":"AuthorizationFailed"},"extra":"(InvalidTemplate)"} garbage',
            '{"error":{"code":["AuthorizationFailed"]}}',
            '{"outer":{"error":{"code":"AuthorizationFailed"}}}',
        )
        for stderr in samples:
            self.assertNotEqual(whatif.classify_azure_failure(stderr), "rbac-denied")

    def test_arm_preview_denial_reports_only_allowlisted_category(self):
        secret = "RAW-IDENTITY-SCOPE-SECRET"
        calls = []
        def runner(argv, **kwargs):
            index = len(calls)
            calls.append(argv)
            if index == 0:
                return SimpleNamespace(returncode=0, stdout=json.dumps(
                    {"id": whatif.SUBSCRIPTION}), stderr="")
            if index == 1:
                return SimpleNamespace(returncode=0, stdout=json.dumps(job()), stderr="")
            return SimpleNamespace(returncode=1, stdout=secret,
                                   stderr="ERROR: (AuthorizationFailed) " + secret)
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "worker-job.bicep"
            source.write_text(SAFE_TEMPLATE)
            with self.assertRaises(whatif.Blocked) as error:
                whatif.execute(runner=runner, environment=ENV, template_path=source)
        self.assertEqual(error.exception.stage, "arm-whatif-preview")
        self.assertEqual(error.exception.error_category, "rbac-denied")
        data = whatif.report(blocked=str(error.exception), stage=error.exception.stage,
                             error_category=error.exception.error_category)
        self.assertEqual(data["arm_error_category"], "rbac-denied")
        self.assertEqual(data["state"], "blocked-unverified")
        self.assertIsNone(data["finding"])
        self.assertNotIn(secret, json.dumps(data))
        self.assertFalse(data["deployment_performed"])
        self.assertEqual(len(calls), 3)

    def test_ambiguous_cli_failure_never_claims_iam_denial(self):
        for stderr in ("failed with status 1", "AuthorizationFailed mentioned in message",
                       "ERROR: (UnknownCode) (AuthorizationFailed)"):
            def runner(argv, **kwargs):
                return SimpleNamespace(returncode=1, stdout="TOKEN",
                                       stderr=stderr)
            with self.assertRaises(whatif.Blocked) as error:
                whatif.run_json(["az", "deployment", "group", "what-if"],
                                runner=runner, stage="arm-whatif-preview")
            self.assertEqual(error.exception.error_category, "unknown")
            self.assertNotIn(stderr, str(error.exception))

    def test_timeout_or_missing_cli_classified_only_as_unavailable(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError("AZURE-SECRET")
        with self.assertRaises(whatif.Blocked) as error:
            whatif.run_json(["az", "deployment", "group", "what-if"],
                            runner=missing, stage="arm-whatif-preview")
        self.assertEqual(error.exception.error_category, "cli-unavailable")
        self.assertNotIn("AZURE-SECRET", str(error.exception))

    def test_rbac_conclusion_cannot_be_published_for_non_arm_stage(self):
        with self.assertRaises(ValueError):
            whatif.report(blocked="blocked", stage="worker-resource-read",
                          error_category="rbac-denied")
        with self.assertRaises(ValueError):
            whatif.report(blocked="blocked", stage="arm-whatif-preview",
                          error_category="untrusted-raw-error")
        self.assertEqual(whatif.report(blocked="blocked", stage="arm-whatif-preview",
                                       error_category="unknown")["arm_error_category"],
                         "unknown")

    def test_main_end_to_end_sanitized_artifacts_with_offline_runner(self):
        secret = "SYNTHETIC-PRIVATE-DIAGNOSTIC"
        cases = [
            ("cli-denial", 2, "ERROR: (AuthorizationFailed) " + secret, "rbac-denied"),
            ("json-denial", 2, '{"error":{"code":"LinkedAuthorizationFailed","message":"' + secret + '"}}', "rbac-denied"),
            ("template", 2, "(InvalidTemplate) " + secret, "template-validation"),
            ("prose", 2, secret + " AuthorizationFailed", "unknown"),
            ("conflicting", 2, "(AuthorizationFailed) first\n(InvalidTemplate) second", "unknown"),
            ("malformed", 2, '{"error":', "unknown"),
            ("oversized", 2, "x" * 65537, "unknown"),
            ("timeout", 2, subprocess.TimeoutExpired("synthetic", 120, stderr=secret), "cli-unavailable"),
            ("missing", 2, FileNotFoundError(secret), "cli-unavailable"),
            ("account", 0, "(AuthorizationFailed) " + secret, "not-classified"),
            ("resource", 1, "(AuthorizationFailed) " + secret, "not-classified"),
            ("success", None, None, "not-classified"),
            ("unsafe-diff", None, None, "not-classified"),
        ]
        real_execute = whatif.execute
        for label, fail_index, failure, category in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = pathlib.Path(tmp) / "worker-job.bicep"
                source.write_text(SAFE_TEMPLATE)
                responses = [{"id": whatif.SUBSCRIPTION}, job(), preview()]
                if label == "unsafe-diff":
                    responses[2] = {"changes": [{"resourceId": whatif.JOB_ID, "changeType": "Delete"}]}
                calls = []
                def runner(argv, **kwargs):
                    index = len(calls)
                    calls.append(argv)
                    if index == fail_index:
                        if isinstance(failure, Exception):
                            raise failure
                        return SimpleNamespace(returncode=1, stdout=secret, stderr=failure)
                    return SimpleNamespace(returncode=0, stdout=json.dumps(responses[index]), stderr="")
                output = io.StringIO()
                with patch.object(whatif, "execute", side_effect=lambda: real_execute(
                        runner=runner, environment=ENV, template_path=source)), \
                     patch.object(whatif, "EVIDENCE", pathlib.Path(tmp) / "data.json"), \
                     patch.object(whatif, "SUMMARY", pathlib.Path(tmp) / "data.md"), \
                     contextlib.redirect_stdout(output):
                    status = whatif.main()
                    raw = whatif.EVIDENCE.read_text()
                    summary = whatif.SUMMARY.read_text()
                data = json.loads(raw)
                success = label == "success"
                stage = ("image-only-diff-classification" if fail_index is None else
                         ["account-inspection", "worker-resource-read", "arm-whatif-preview"][fail_index])
                self.assertEqual(status, 0 if success else 2)
                self.assertEqual(data["diagnostic_stage"], stage)
                self.assertEqual(data["arm_error_category"], category)
                self.assertEqual(data["state"], "image-only-preview" if success else "blocked-unverified")
                self.assertEqual(bool(data["finding"]), success)
                self.assertEqual(data["image_digest"], whatif.DIGEST)
                for flag in ("deployment_performed", "iam_modified", "worker_started", "paid_provider_called"):
                    self.assertFalse(data[flag])
                for sensitive in (secret, "AuthorizationFailed", "LinkedAuthorizationFailed"):
                    self.assertNotIn(sensitive, raw + summary + output.getvalue())
                if not success:
                    self.assertIn(category, summary)
                self.assertEqual(len(calls), 3 if fail_index is None else fail_index + 1)

    def test_workflow_main_only_manual_no_write_actions(self):
        workflow = (ROOT / ".github/workflows/azure-worker-whatif.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("environment: azure-readonly-audit", workflow)
        self.assertIn("AZURE_EXTENSION_USE_DYNAMIC_INSTALL: 'no'", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn(whatif.REVIEWED_SHA, workflow)
        self.assertNotIn("az deployment group create", workflow)
        self.assertNotIn("AZURE_DEPLOY_CLIENT_ID", workflow)
        self.assertNotIn("AZURE_CLIENT_ID: ", workflow)


if __name__ == "__main__":
    unittest.main()

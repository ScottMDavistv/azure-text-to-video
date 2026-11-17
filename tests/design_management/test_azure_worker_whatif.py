"""No Azure access required: fail-closed manual what-if workflow regressions."""
import importlib.util
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

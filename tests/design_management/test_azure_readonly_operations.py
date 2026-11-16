"""Offline regressions for the no-write Azure operations evidence collector."""
import importlib.util
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "azure_readonly_operations", ROOT / "scripts" / "azure_readonly_operations.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
RG_BASE = "/subscriptions/" + audit.EXPECTED_SUBSCRIPTION + "/resourceGroups/" + audit.RG


def resource(provider, kind, name):
    return RG_BASE + "/providers/" + provider + "/" + kind + "/" + name


def sample(*, immutable=True):
    worker_id = resource("Microsoft.ManagedIdentity", "userAssignedIdentities",
                         audit.WORKER_IDENTITY)
    login = audit.ACR + ".azurecr.io"
    image = login + "/textvideo-worker@" + "sha256:" + "a" * 64 if immutable else (
        login + "/textvideo-worker:mutable")
    job = {
        "id": resource("Microsoft.App", "jobs", audit.JOB),
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {worker_id: {}}},
        "properties": {
            "workloadProfileName": "Consumption",
            "configuration": {"triggerType": "Manual", "secrets": [],
                              "registries": [{"server": login, "identity": worker_id}]},
            "template": {"containers": [{
                "image": image, "command": ["python"],
                "args": ["worker_runtime.py", "--check"],
                "env": [{"name": name, "value": expected}
                        for name, expected in audit.FLAGS.items()
                        if name != "ENABLE_PAID_FAL_WORKER"],
            }]},
        },
    }
    registry = {"id": resource("Microsoft.ContainerRegistry", "registries", audit.ACR),
                "loginServer": login}
    def identity(name, client, principal):
        return {"id": resource("Microsoft.ManagedIdentity", "userAssignedIdentities", name),
                "clientId": client, "principalId": principal}
    worker = identity(audit.WORKER_IDENTITY, "worker-client", "11111111-1111-1111-1111-111111111111")
    preview = identity(audit.AUDIT_IDENTITY, audit.PREVIEW_CLIENT_ID,
                       "22222222-2222-2222-2222-222222222222")
    deploy = identity(audit.DEPLOY_IDENTITY, "different-client",
                      "33333333-3333-3333-3333-333333333333")
    return [job, registry, worker, preview, deploy, [{"roleDefinitionName": "Reader"}], []]


class AzureReadOnlyOperationsTests(unittest.TestCase):
    def test_all_green_fixture_is_preflight_only_not_deployment_authorization(self):
        report = audit.evaluate(*sample())
        self.assertEqual(report["deployment_readiness"], "preflight-only")
        self.assertFalse(report["deployment_performed"])
        self.assertFalse(report["provider_called"])
        self.assertTrue(all(f["status"] == "pass" for f in report["findings"]))
        self.assertIn("Child-resource-specific RBAC and other subscriptions/tenants",
                      report["not_verified"])

    def test_existing_tagged_worker_is_blocked_not_misrepresented_as_immutable(self):
        report = audit.evaluate(*sample(immutable=False))
        self.assertEqual(report["deployment_readiness"], "blocked")
        self.assertIn("immutable worker image",
                      [x["check"] for x in report["findings"] if x["status"] == "block"])

    def test_live_provider_or_secret_disables_readiness(self):
        values = sample()
        job = values[0]
        env = job["properties"]["template"]["containers"][0]["env"]
        env[0]["value"] = "fal"
        job["properties"]["configuration"]["secrets"] = [{"name": "fal-secret",
                                                               "value": "never-report-me"}]
        report = audit.evaluate(*values)
        self.assertEqual(report["deployment_readiness"], "blocked")
        self.assertNotIn("never-report-me", json.dumps(report))

    def test_wrong_worker_registry_identity_blocks(self):
        values = sample()
        values[0]["properties"]["configuration"]["registries"][0]["identity"] = "system"
        self.assertEqual(audit.evaluate(*values)["deployment_readiness"], "blocked")

    def test_overprivileged_preview_and_deploy_roles_block(self):
        values = sample()
        values[5] = [{"roleDefinitionName": "Contributor"}]
        values[6] = [{"roleDefinitionName": "Owner"}]
        report = audit.evaluate(*values)
        blocked = {x["check"] for x in report["findings"] if x["status"] == "block"}
        self.assertIn("preview resource-group RBAC", blocked)
        self.assertIn("deploy resource-group RBAC", blocked)

    def test_reject_wrong_subscription_and_outside_resource_group(self):
        values = sample()
        values[0]["id"] = values[0]["id"].replace(audit.RG, "rg-outside")
        with self.assertRaises(audit.AuditError):
            audit.evaluate(*values)
        with patch.object(audit, "run_read", return_value={"id": "wrong"}):
            with self.assertRaises(audit.AuditError):
                audit.collect(reader=audit.run_read)

    def test_only_argv_read_calls_and_no_leaked_azure_exception(self):
        class Result:
            stdout = '{"id": "ok"}'
        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["az", "account", "show"])
            self.assertEqual(command[-2:], ["--output", "json"])
            self.assertFalse(kwargs.get("shell", False))
            return Result()
        self.assertEqual(audit.run_read(["az", "account", "show"], runner=runner),
                         {"id": "ok"})
        for cmd in (["az", "deployment", "group", "create"],
                    ["az", "role", "assignment", "create"],
                    ["az", "identity", "credential", "list"]):
            with self.assertRaises(audit.AuditError):
                audit.run_read(cmd, runner=runner)
        secret = subprocess.CalledProcessError(1, "az", stderr="secret_token_value")
        with self.assertRaises(audit.AuditError) as cm:
            audit.run_read(["az", "account", "show"],
                           runner=lambda *args, **kwargs: (_ for _ in ()).throw(secret))
        self.assertNotIn("secret_token_value", str(cm.exception))

    def test_report_is_sanitized_and_no_ids_or_secrets_are_serialized(self):
        report = audit.evaluate(*sample())
        self.assertNotIn("22222222-2222", json.dumps(report))
        self.assertNotIn("33333333-3333", json.dumps(report))
        self.assertNotIn(audit.PREVIEW_CLIENT_ID, json.dumps(report))
        with patch.object(audit, "RESULT_FILE") as output:
            with patch.object(audit, "SUMMARY_FILE") as summary:
                audit.write_report(report)
                result_text = output.write_text.call_args.args[0]
                summary_text = summary.write_text.call_args.args[0]
                self.assertIn("preflight-only", result_text)
                self.assertIn("Not verified", summary_text)
                self.assertNotIn("22222222-2222", summary_text)

    def test_workflow_read_only_identity_and_event_isolation(self):
        workflow = (ROOT / ".github/workflows/azure-readonly-operations.yml").read_text()
        self.assertIn("environment: dev-deployment", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("AZURE_PREVIEW_CLIENT_ID", workflow)
        self.assertNotIn("AZURE_DEPLOY_CLIENT_ID", workflow)
        self.assertNotIn("az deployment group create", workflow)
        self.assertNotIn("azure/login@v2\n        with:\n          client-id: ${{ vars.AZURE_DEPLOY_CLIENT_ID }}", workflow)


if __name__ == "__main__":
    unittest.main()

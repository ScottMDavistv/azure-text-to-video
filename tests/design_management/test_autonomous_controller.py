import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("autonomous_controller", ROOT / "scripts/autonomous_controller.py")
import sys
sys.path.insert(0, str(ROOT / "scripts"))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class ControllerTests(unittest.TestCase):
    def test_two_agent_cap_and_conservative_counts(self):
        issues = [{"state":"open","assignees":[{"login":"copilot-swe-agent[bot]"}]}]
        prs = [{"state":"open","user":{"login":"copilot-swe-agent[bot]"}}]
        self.assertEqual(controller.active_slots(issues, prs), 2)
    def test_missing_token_fails_before_dispatch(self):
        with patch.object(controller, "pages", side_effect=[
            [{"state":"open","number":10,"body":"<!-- design-agent-task:video-job-api-contract -->","assignees":[]}],
            []]):
            with self.assertRaisesRegex(RuntimeError, "user-to-server"):
                controller.dispatch("owner/repo", "readtoken", "", live=True)
    def test_dispatch_plan_blocks_azure_iam_and_dependencies(self):
        from design_agent_coordination import validate
        import json
        tasks = json.loads(controller.MANIFEST.read_text())["tasks"]
        index = {
            "video-job-api-contract": {"number": 3,"state":"open","body":"<!-- design-agent-task:video-job-api-contract -->"},
            "azure-reader-monitoring": {"number": 4,"state":"open","body":"<!-- design-agent-task:azure-reader-monitoring -->"},
        }
        plan = controller.dispatch_plan(tasks, index, slots=0)
        self.assertEqual([p[2]["id"] for p in plan], ["video-job-api-contract"])
        self.assertEqual(controller.dispatch_plan(tasks, index, slots=2), [])
    def test_dry_merge_never_invokes_gh(self):
        with patch.object(controller, "auto_merge_plan", return_value=[]), patch.object(controller.subprocess, "run", side_effect=AssertionError):
            self.assertEqual(controller.merge("o/r","token", live=False), [])
    def test_bounded_http_errors_do_not_retry_write(self):
        import urllib.error
        err = urllib.error.HTTPError("url", 503, "unavailable", {}, None)
        with patch.object(controller.urllib.request, "urlopen", side_effect=err) as mocked:
            with self.assertRaises(urllib.error.HTTPError):
                controller.api("POST", "/repos/o/r/issues", "token", {"title":"x"}, attempts=1)
        self.assertEqual(mocked.call_count, 1)
    def test_bounded_repairs_wait_for_final_ci(self):
        pr = {"state":"open", "draft":False,
              "user":{"login":"copilot-swe-agent[bot]"},
              "head":{"sha":"a"*40}}
        failing = [{"name":"Backend Python Tests", "head_sha":"a"*40,
                    "event":"pull_request", "status":"completed",
                    "conclusion":"failure", "created_at":"2026-11-16T11:00:00Z","id":1},
                   {"name":"Security Checks", "head_sha":"a"*40,
                    "event":"pull_request", "status":"completed",
                    "conclusion":"success", "created_at":"2026-11-16T11:00:00Z","id":2}]
        self.assertEqual(controller.repair_decision(pr, failing, [])["action"], "request-repair")
        pending = [dict(failing[0], status="in_progress", conclusion=None)] + failing[1:]
        self.assertEqual(controller.repair_decision(pr, pending, [])["action"], "hold")
        duplicate = [{"body":"<!-- design-agent-repair:attempt:1 -->\\n<!-- design-agent-repair:head:"+"a"*40+" -->"}]
        self.assertEqual(controller.repair_decision(pr, failing, duplicate)["action"], "hold")
        exhausted = [{"body":"<!-- design-agent-repair:attempt:1 -->"},
                     {"body":"<!-- design-agent-repair:attempt:2 -->"}]
        self.assertEqual(controller.repair_decision(pr, failing, exhausted)["action"], "escalate")

    def test_decision_deduplicates_closed_issue(self):
        plan = [{"number":8,"head_sha":"a"*40,"eligible":False,"reason":"protected path keyword"}]
        old = [{"number":5,"body":"<!-- design-management-decision:sensitive-pr-8 -->","state":"closed"}]
        with patch.object(controller, "pages", return_value=old):
            self.assertEqual(controller.decisions("o/r","token",plan,live=False), [])

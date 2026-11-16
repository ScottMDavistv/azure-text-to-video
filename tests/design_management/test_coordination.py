"""Offline tests: manifest validation, work queues, issue creation, completion guidance."""
import importlib.util
import json
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("design_agent_coordination", ROOT / "scripts/design_agent_coordination.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tasks = json.loads((ROOT / "docs/design-management-tasks.json").read_text())["tasks"]

    def test_manifest_valid_and_no_sensitive_execution(self):
        self.assertEqual(len(mod.validate(self.tasks)), 5)
        self.assertTrue(all(t["approval"] == "approved-development" and t["sensitive"] is False for t in self.tasks))

    def test_reject_cycle(self):
        a = dict(self.tasks[0], depends_on=["synthetic-orchestrator"])
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            mod.validate([a] + self.tasks[1:])

    def test_reject_sensitive(self):
        a = dict(self.tasks[0], sensitive=True)
        with self.assertRaisesRegex(ValueError, "Sensitive"):
            mod.validate([a] + self.tasks[1:])

    def test_dependency_is_blocked_until_completed(self):
        index = {"video-job-api-contract": {"state": "open", "html_url": "https://example.com"}}
        queues = mod.work_queues(self.tasks, index)
        frontend = next(x for x in queues["frontend"] if x["id"] == "frontend-video-journey")
        self.assertEqual(frontend["status"], "unpublished")
        self.assertIn("video-job-api-contract", frontend["dependencies_remaining"])
        index["video-job-api-contract"]["state"] = "closed"
        index["video-job-api-contract"]["state_reason"] = "not_planned"
        self.assertTrue(mod.dependency_status(self.tasks[2], mod.validate(self.tasks), index))
        index["video-job-api-contract"]["state_reason"] = "completed"
        self.assertTrue(mod.dependency_status(self.tasks[2], mod.validate(self.tasks), index))
        index["video-job-api-contract"]["body"] = "<!-- design-agent-accepted:video-job-api-contract -->"
        self.assertFalse(mod.dependency_status(self.tasks[2], mod.validate(self.tasks), index))

    def test_issue_index_skips_pr_and_deduplicates(self):
        marker = mod.task_marker("video-job-api-contract")
        issue = {"body": marker, "number": 42}
        self.assertEqual(mod.issue_index([issue, dict(issue, pull_request={"url": "https://github.com/pr"})])["video-job-api-contract"]["number"], 42)
        with self.assertRaises(RuntimeError):
            mod.issue_index([issue, issue])

    def test_dry_run_never_writes(self):
        with patch.object(mod, "http_json", side_effect=AssertionError("no writes")):
            results = mod.publish("owner/repo", "token", self.tasks, {}, dry_run=True)
        self.assertTrue(any(x["action"] == "would-create" for x in results))

    def test_merged_pr_is_only_candidate(self):
        issue = {"number": 24}
        prs = [{"number": 1, "body": "Fixes #24", "base": {"ref": "main"}, "merged_at": None},
               {"number": 2, "body": "Closes #24", "base": {"ref": "main"}, "merged_at": "2026-11-16"},
               {"number": 3, "body": "#240", "base": {"ref": "main"}, "merged_at": "2026-11-16"}]
        self.assertEqual([x["number"] for x in mod.candidate_completion(issue, prs)], [2])

    def test_completion_requires_exact_sha_checks(self):
        pr = {"merge_commit_sha": "a" * 40}
        runs = [{"head_sha": "a" * 40, "name": "Backend Python Tests",
                 "event": "push", "status": "completed", "conclusion": "success", "html_url": "https://ci/1"},
                {"head_sha": "b" * 40, "name": "Security Checks",
                 "event": "push", "status": "completed", "conclusion": "success", "html_url": "https://ci/2"}]
        result = mod.verify_completion(pr, runs, self.tasks[0])
        self.assertEqual(result["status"], "ci-unverified")
        runs[1]["head_sha"] = "a" * 40
        result = mod.verify_completion(pr, runs, self.tasks[0])
        self.assertEqual(result["status"], "ci-review-ready")
        self.assertTrue(result["manual_acceptance_required"])

    def test_owner_acceptance_requires_completed_and_marker(self):
        issue = {"state": "open", "state_reason": "completed",
                 "body": "<!-- design-agent-accepted:video-job-api-contract -->"}
        self.assertFalse(mod.owner_accepted("video-job-api-contract", issue))
        issue["state"] = "closed"
        self.assertTrue(mod.owner_accepted("video-job-api-contract", issue))
        issue["body"] = ""
        self.assertFalse(mod.owner_accepted("video-job-api-contract", issue))

    def test_issue_body_never_authorizes_merge(self):
        text = mod.issue_body(self.tasks[0])
        self.assertIn("No automatic merge", text)
        self.assertIn(mod.task_marker(self.tasks[0]["id"]), text)

if __name__ == "__main__":
    unittest.main()

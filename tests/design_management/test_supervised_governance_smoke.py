"""Supervised offline smoke tests: no GitHub writes, merges, or paid services."""
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import autonomous_controller as controller
import verify_design_management_publication as publication


class SupervisedSmokeTests(unittest.TestCase):
    def test_audit_publication_requires_current_exact_snapshot(self):
        report = "### Automated evidence snapshot — 2026-11-16 18:00 UTC\nEvidence."
        current = {"body": "<!-- design-management-report:start -->\n" + report +
                   "\n<!-- design-management-report:end -->"}
        self.assertTrue(publication.verify_published_snapshot(
            "owner/repo", "dummy", report, fetch=current))
        with self.assertRaisesRegex(RuntimeError, "does not contain"):
            publication.verify_published_snapshot(
                "owner/repo", "dummy", report, fetch={"body": "stale snapshot"})
        with self.assertRaises(ValueError):
            publication.verify_published_snapshot(
                "owner/repo", "dummy", "not an audit", fetch=current)

    def test_controller_modes_are_non_mutating_with_all_toggles_off(self):
        env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_TOKEN": "test-only-token",
            "AUTONOMOUS_ENGINEERING_ENABLED": "false",
            "COPILOT_DISPATCH_ENABLED": "false",
            "COPILOT_REPAIR_ENABLED": "false",
            "AUTO_MERGE_ENABLED": "false",
            "DECISION_INBOX_ENABLED": "false",
            "PROTECTED_MAIN_VERIFIED": "false",
        }
        with patch.dict(controller.os.environ, env, clear=True):
            with (
                patch.object(controller, "dispatch", return_value=[]) as dispatch,
                patch.object(controller, "repair", return_value=[]) as repair,
                patch.object(controller, "merge", return_value=[]) as merge,
                patch.object(controller, "auto_merge_plan", return_value=[]),
                patch.object(controller, "decisions", return_value=[]) as decisions,
            ):
                for mode in ("dispatch", "repair", "merge", "decisions"):
                    with patch.object(sys, "argv", ["autonomous_controller.py", "--mode", mode]):
                        controller.main()
                self.assertFalse(dispatch.call_args.kwargs["live"])
                self.assertFalse(repair.call_args.kwargs["live"])
                self.assertFalse(merge.call_args.kwargs["live"])
                self.assertFalse(decisions.call_args.kwargs["live"])

    def test_existing_schedule_and_controller_opt_in_are_visible(self):
        audit = (ROOT / ".github/workflows/design-management-audit.yml").read_text()
        controller_workflow = (ROOT / ".github/workflows/autonomous-engineering.yml").read_text()
        self.assertIn("cron: '0 2,14 * * *'", audit)
        self.assertIn("workflow_dispatch:", audit)
        self.assertIn("vars.AUTONOMOUS_ENGINEERING_ENABLED == 'true'", controller_workflow)


if __name__ == "__main__":
    unittest.main()

"""Strict autonomous classification tests. No network or GitHub token."""
import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "autonomous_policy.py"
spec = importlib.util.spec_from_file_location("autonomous_policy", PATH)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

def file(path, **kwargs):
    return {"filename": path, "status": "modified", "additions": 2, "deletions": 1,
            "patch": "@@ -1,1 +1,2 @@\n-old\n+new", **kwargs}

class AutonomousPolicyTests(unittest.TestCase):
    def test_allows_narrow_ui_docs_and_css_only(self):
        self.assertEqual(policy.classify([file("docs/ui/navigation.md"), file("frontend/src/App.css")])["risk"], "low")
    def test_sensitive_and_mixed_fail_closed(self):
        for p in ["backend/finance_controls.py", "infra/main.bicep",
                  ".github/workflows/deploy-infrastructure.yml",
                  "frontend/src/entraAuth.ts", "scripts/autonomous_policy.py",
                  "docs/ui/security-exception.md", "frontend/src/App.tsx"]:
            with self.subTest(p=p):
                self.assertEqual(policy.classify([file("docs/ui/usage.md"), file(p)])["risk"], "sensitive")
    def test_diff_content_blocks_active_css_and_binary(self):
        self.assertEqual(policy.classify([file("frontend/src/App.css",
            patch="@@ -1 +1 @@\n+@import url(https://untrusted.invalid/style.css)")])["risk"], "sensitive")
        self.assertEqual(policy.classify([file("docs/ui/help.md", patch=None)])["risk"], "sensitive")
        self.assertEqual(policy.classify([file("docs/ui/help.md",
            patch="@@ -1 +1 @@\n+<script>alert(1)</script>")])["risk"], "sensitive")
    def test_reject_renames_and_missing_inventory(self):
        self.assertEqual(policy.classify([file("docs/ui/help.md", status="renamed")])["risk"], "sensitive")
        self.assertEqual(policy.classify([], complete=False)["risk"], "sensitive")
        self.assertEqual(policy.classify([file("../docs/ui/help.md")])["risk"], "sensitive")
    def test_no_auto_merge_without_every_gate(self):
        sha = "a" * 40
        pr = {"state": "open", "draft": False,
              "user": {"login": "copilot-swe-agent[bot]", "type": "Bot"},
              "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
              "head": {"sha": sha, "repo": {"full_name": "owner/repo"}}}
        good = [file("docs/ui/help.md")]
        ok, _ = policy.may_queue_auto_merge(pr, good, enabled=True,
            required_checks={"Backend Python Tests", "Security Checks"},
            protected_main_verified=True)
        self.assertTrue(ok)
        for overrides in [dict(enabled=False), dict(protected_main_verified=False),
                          dict(required_checks={"Backend Python Tests"})]:
            opts = dict(enabled=True, protected_main_verified=True,
                        required_checks={"Backend Python Tests", "Security Checks"})
            opts.update(overrides)
            self.assertFalse(policy.may_queue_auto_merge(pr, good, **opts)[0])
        self.assertFalse(policy.may_queue_auto_merge(dict(pr, draft=True), good,
            enabled=True, protected_main_verified=True,
            required_checks={"Backend Python Tests", "Security Checks"})[0])
    def test_human_lookalike_cannot_auto_merge(self):
        pr = {"state": "open", "draft": False,
              "user": {"login": "copilot-swe-agent", "type": "User"},
              "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
              "head": {"sha": "a" * 40, "repo": {"full_name": "owner/repo"}}}
        self.assertFalse(policy.may_queue_auto_merge(
            pr, [file("docs/ui/help.md")], enabled=True,
            required_checks={"Backend Python Tests", "Security Checks"},
            protected_main_verified=True)[0])

    def test_retry_limit(self):
        self.assertTrue(policy.bounded_retries(0))
        self.assertTrue(policy.bounded_retries(1))
        self.assertFalse(policy.bounded_retries(2))
        self.assertFalse(policy.bounded_retries(-1))

"""Security-review remediation router fails closed and never dispatches."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("security_remediation",
                                              ROOT / "scripts/security_review_remediation.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
HEAD = "a" * 40
PR = {"number": 46, "state": "open", "draft": True,
      "head": {"sha": HEAD}, "base": {"ref": "main",
      "repo": {"full_name": "ScottMDavistv/azure-text-to-video"}}}
REVIEW = {"commit_id": HEAD, "state": "COMMENTED",
          "user": {"login": "copilot-pull-request-reviewer", "type": "Bot"},
          "comments": [{"path": "backend/finance_authorization.py",
                        "body": "Verify unique request transaction atomicity",
                        "blocking": True}]}


def assess(pr=PR, reviews=None, issues=None, login="copilot-pull-request-reviewer",
           kind="Bot"):
    return mod.plan(pr, [REVIEW] if reviews is None else reviews,
                    [] if issues is None else issues,
                    reviewer_login=login, reviewer_type=kind)


class SecurityRemediationTests(unittest.TestCase):
    def test_one_verified_finding_creates_one_plan(self):
        result = assess()
        self.assertEqual(result["state"], "review-findings")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["action"], "would-create-remediation-issue")
        self.assertIn("held-until", result["tasks"][0]["assignment"])

    def test_duplicates_or_prior_issues_do_not_create_more(self):
        marker = assess()["tasks"][0]["marker"]
        self.assertEqual(assess(reviews=[REVIEW, REVIEW])["tasks"].__len__(), 1)
        self.assertEqual(assess(issues=[{"body": "<!-- " + marker + " -->"}])["tasks"], [])

    def test_stale_review_never_authenticates(self):
        bad = dict(REVIEW, commit_id="b" * 40)
        self.assertEqual(assess(reviews=[bad])["tasks"], [])

    def test_fake_reviewer_and_untrusted_actor_fail_closed(self):
        self.assertEqual(assess(login="someone-else")["state"], "hold")
        self.assertEqual(assess(kind="User")["state"], "hold")
        forged = dict(REVIEW, user={"login": "someone-else", "type": "Bot"})
        self.assertEqual(assess(reviews=[forged])["tasks"], [])

    def test_protected_main_and_draft_required(self):
        for mutation in ({"draft": False}, {"state": "closed"},
                         {"head": {"sha": "INVALID"}},
                         {"base": {"ref": "develop", "repo": PR["base"]["repo"]}}):
            self.assertEqual(assess(pr=dict(PR, **mutation))["state"], "hold")

    def test_partial_review_inventory_holds(self):
        self.assertEqual(mod.plan(PR, None, [], reviewer_login="copilot-pull-request-reviewer",
                                  reviewer_type="Bot")["state"], "hold")
        wrong = dict(REVIEW, comments="not-a-list")
        self.assertEqual(assess(reviews=[wrong])["state"], "hold")

    def test_unknown_paths_sensitive(self):
        self.assertTrue(mod.sensitive([], complete=True))
        self.assertTrue(mod.sensitive(["frontend/src/entraAuth.ts"], complete=True))
        self.assertTrue(mod.sensitive([".github/workflows/ci.yml"], complete=True))
        self.assertTrue(mod.sensitive(["docs/readme.md"], complete=False))
        self.assertFalse(mod.sensitive(["docs/readme.md"], complete=True))

    def test_agent_cap_and_credit_gates(self):
        ok = dict(active_agents=1, reviewer_active=True, isolated_secrets=True,
                  exact_head_review=True, included_credits_verified=True,
                  paid_overages_enabled=False, independent_coder=True)
        self.assertTrue(mod.can_dispatch_coder(**ok))
        for key, bad in (("active_agents", 2), ("reviewer_active", False),
                         ("isolated_secrets", False), ("exact_head_review", False),
                         ("included_credits_verified", False),
                         ("paid_overages_enabled", True), ("independent_coder", False)):
            self.assertFalse(mod.can_dispatch_coder(**dict(ok, **{key: bad})))
        self.assertFalse(mod.can_dispatch_coder(**dict(ok, active_agents=None)))


class SecurityFirstRoutingTests(unittest.TestCase):
    def test_security_remediation_before_ordinary(self):
        tasks = [
            {"id": "feature", "priority_class": "ordinary", "state": "queued"},
            {"id": "minor", "priority_class": "other-security", "state": "queued",
             "independent_reviewer": True, "distinct_coder": True},
            {"id": "critical", "priority_class": "critical-security", "state": "queued",
             "independent_reviewer": True, "distinct_coder": True},
        ]
        result = mod.next_assignable(
            tasks, active_agents=1, capacity_verified=True, security_gates_verified=True,
            included_credits_verified=True, paid_overages_enabled=False)
        self.assertEqual(result, {"action": "would-dispatch-security", "task_id": "critical"})

    def test_blocked_security_prevents_ordinary_dispatch(self):
        tasks = [{"id": "security", "priority_class": "high-security", "state": "blocked"},
                 {"id": "feature", "priority_class": "ordinary", "state": "queued"}]
        result = mod.next_assignable(
            tasks, active_agents=0, capacity_verified=True, security_gates_verified=True,
            included_credits_verified=True, paid_overages_enabled=False)
        self.assertEqual(result["action"], "hold")

    def test_fails_closed_if_agents_or_credit_unknown(self):
        tasks = [{"id": "feature", "priority_class": "ordinary", "state": "queued"}]
        for agents in (None, 2, 3):
            result = mod.next_assignable(
                tasks, active_agents=agents, capacity_verified=True,
                security_gates_verified=True, included_credits_verified=True,
                paid_overages_enabled=False)
            self.assertEqual(result["action"], "hold")
        result = mod.next_assignable(
            tasks, active_agents=0, capacity_verified=True,
            security_gates_verified=True, included_credits_verified=False,
            paid_overages_enabled=False)
        self.assertEqual(result["action"], "hold")

    def test_reviewer_coder_separation_required(self):
        tasks = [{"id": "security", "priority_class": "other-security", "state": "queued",
                  "independent_reviewer": True, "distinct_coder": False}]
        result = mod.next_assignable(
            tasks, active_agents=1, capacity_verified=True,
            security_gates_verified=True, included_credits_verified=True,
            paid_overages_enabled=False)
        self.assertEqual(result["action"], "hold")

    def test_only_owner_decisions_notify(self):
        for event in ({"reason": "ci-green", "requires_owner_decision": True},
                      {"reason": "repair-attempts-exhausted", "requires_owner_decision": False},
                      {"reason": "new-commit", "requires_owner_decision": False}):
            self.assertFalse(mod.should_notify_owner(event))
        self.assertTrue(mod.should_notify_owner(
            {"reason": "security-sensitive-merge", "requires_owner_decision": True}))

    def test_untrusted_priorities_fail_closed(self):
        with self.assertRaises(ValueError):
            mod.ordered_queue([{"id": "x", "priority_class": "unknown"}])


if __name__ == "__main__":
    unittest.main()

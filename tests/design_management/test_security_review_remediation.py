"""Offline regressions for fail-closed security remediation planning.

The verifier below is a test-only stand-in for authenticated protected-main
intake; it is NOT evidence that a trusted production collector exists.
"""
import importlib.util
import pathlib
import unittest
from copy import deepcopy

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "security_remediation", ROOT / "scripts/security_review_remediation.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
HEAD = "a" * 40
BOT = "copilot-pull-request-reviewer[bot]"
REPO = "ScottMDavistv/azure-text-to-video"
PR = {"number": 55, "state": "open", "draft": True, "user": {"login": "owner"},
      "head": {"sha": HEAD},
      "base": {"ref": "main", "repo": {"full_name": REPO}}}
# GitHub REST review and inline comment response shapes: no 'blocking' property.
COMMENT = {"id": 4598442710, "path": "scripts/security_review_remediation.py",
           "body": "Unverified reviewer evidence can bypass the security gate",
           "commit_id": HEAD, "line": 60,
           "user": {"login": BOT, "type": "Bot"}}
REVIEW = {"id": 78642, "commit_id": HEAD, "state": "COMMENTED",
          "user": {"login": BOT, "type": "Bot"},
          "comments": [COMMENT]}
CHANGED = [{"filename": "scripts/security_review_remediation.py", "status": "added"}]
ATTESTATION = {"verified": True, "head_sha": HEAD, "reviewer_login": BOT,
               "coding_actor_login": "implementation-agent",
               "commit_actor_logins": ["owner", "implementation-agent"],
               "coding_session_ids": ["coder-session-1"],
               "reviewer_session_id": "review-session-2",
               "all_commit_actors_verified": True,
               "all_sessions_verified": True, "pagination_verified": True,
               "issue_authors_verified": True, "change_history_verified": True}


def protected_collector_test_stub(evidence, pr, reviews, issues, changed_files):
    # Tests use a stub; real operation requires authenticated external intake.
    return deepcopy(evidence)


def assess(*, pr=None, reviews=None, issues=None, changed=None, attestation=None,
           verify=protected_collector_test_stub, login=BOT, kind="Bot", **options):
    return mod.plan(deepcopy(PR if pr is None else pr),
                    deepcopy([REVIEW] if reviews is None else reviews),
                    deepcopy([] if issues is None else issues),
                    reviewer_login=login, reviewer_type=kind,
                    verified_evidence=deepcopy(ATTESTATION if attestation is None else attestation),
                    verify_evidence=verify,
                    changed_files=deepcopy(CHANGED if changed is None else changed),
                    **dict(dict(reviews_complete=True, comments_complete=True,
                                issues_complete=True, changed_files_complete=True), **options))


class SecurityRemediationTests(unittest.TestCase):
    def test_real_github_comment_without_blocking_is_not_dropped(self):
        result = assess()
        self.assertEqual(result["state"], "review-findings")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["finding"], COMMENT["body"])
        self.assertTrue(result["tasks"][0]["security_sensitive"])

    def test_no_protected_verifier_means_hold(self):
        self.assertEqual(assess(verify=None)["state"], "hold")
        self.assertEqual(mod.plan(PR, [REVIEW], [], reviewer_login=BOT,
                                  reviewer_type="Bot")["state"], "hold")
        self.assertEqual(assess(verify=lambda *args: True)["state"], "hold")
        self.assertEqual(assess(verify=lambda *args: (_ for _ in ()).throw(ValueError()))["state"], "hold")

    def test_spoofed_or_missing_actor_evidence_fails_closed(self):
        for patch in ({"reviewer_login": "other"}, {"coding_actor_login": BOT},
                      {"commit_actor_logins": ["owner", BOT]},
                      {"reviewer_session_id": "coder-session-1"},
                      {"coding_session_ids": []}, {"verified": False},
                      {"head_sha": "b" * 40}, {"all_commit_actors_verified": False},
                      {"all_sessions_verified": False}, {"pagination_verified": False},
                      {"issue_authors_verified": False}, {"change_history_verified": False}):
            with self.subTest(patch=patch):
                self.assertEqual(assess(attestation={**ATTESTATION, **patch})["state"], "hold")

    def test_real_bot_login_and_fake_login(self):
        self.assertEqual(assess(login="copilot-pull-request-reviewer")["state"], "hold")
        self.assertEqual(assess(login="owner")["state"], "hold")
        self.assertEqual(assess(kind="User")["state"], "hold")
        forged = deepcopy(REVIEW)
        forged["user"]["login"] = "fake-bot"
        self.assertEqual(assess(reviews=[forged])["state"], "hold")

    def test_stale_or_incomplete_reviews_hold(self):
        stale = deepcopy(REVIEW)
        stale["commit_id"] = "b" * 40
        self.assertEqual(assess(reviews=[stale])["state"], "hold")
        self.assertEqual(assess(reviews=[])["state"], "hold")
        self.assertEqual(assess(reviews=[{**REVIEW, "comments": None}])["state"], "hold")
        self.assertEqual(assess(reviews=[{**REVIEW, "comments": []}])["state"], "no-new-findings")
        self.assertEqual(assess(reviews=[{**REVIEW, "state": "CHANGES_REQUESTED",
                                          "comments": []}])["state"], "hold")
        self.assertEqual(assess(reviews=[{**REVIEW, "comments": [{"body": "missing path"}]}])["state"], "hold")

    def test_pagination_defaults_and_partial_lists_hold(self):
        for key in ("reviews_complete", "comments_complete", "issues_complete",
                    "changed_files_complete"):
            self.assertEqual(assess(**{key: False})["state"], "hold")
        self.assertEqual(assess(issues=[None])["state"], "hold")
        self.assertEqual(assess(changed=[])["state"], "hold")

    def test_malicious_marker_does_not_suppress_findings(self):
        marker = assess()["tasks"][0]["marker"]
        poisoned = {"number": 123, "body": "<!-- " + marker + " -->",
                    "user": {"login": "attacker"}, "state": "open",
                    "marker": marker, "repository": REPO}
        self.assertEqual(len(assess(issues=[poisoned])["tasks"]), 1)
        self.assertEqual(len(assess(issues=[{**poisoned, "state": "closed"}])["tasks"]), 1)
        trusted = {**poisoned, "trusted_remediation": True,
                   "author_assurance": "trusted-controller"}
        self.assertEqual(assess(issues=[trusted])["tasks"], [])
        self.assertEqual(len(assess(issues=[{**trusted, "marker": marker + "a"}])["tasks"]), 1)

    def test_duplicate_github_comments_create_one_candidate(self):
        self.assertEqual(len(assess(reviews=[REVIEW, REVIEW])["tasks"]), 1)

    def test_renamed_sensitive_file_cannot_appear_documentation_only(self):
        renamed = [{"filename": "docs/guide.md", "status": "renamed",
                    "previous_filename": ".github/workflows/deploy.yml"}]
        self.assertTrue(assess(changed=renamed)["tasks"][0]["security_sensitive"])
        self.assertEqual(assess(changed=[{"filename": "docs/guide.md",
                                           "status": "renamed"}])["state"], "hold")
        self.assertFalse(mod.sensitive(["docs/guide.md"], complete=True,
                                       previous_paths=["docs/old.md"]))
        self.assertTrue(mod.sensitive(["docs/guide.md"], complete=True,
                                      previous_paths=["backend/main.py"]))

    def test_paths_invalid_and_unknown_fail_closed(self):
        for path in ("backend/main.py", "../x", "/docs/a.md", "", None,
                     "docs/../a.md", "docs//a.md", "docs/a.md\\\\z", "docs/a.md\\x00"):
            with self.subTest(path=path):
                self.assertTrue(mod.sensitive([path], complete=True))
        self.assertFalse(mod.sensitive(["docs/readme.md"], complete=True))
        self.assertTrue(mod.sensitive(["docs/readme.md"], complete=False))

    def test_pr_constraints_and_fingerprint(self):
        for patch in ({"draft": False}, {"state": "closed"},
                      {"head": {"sha": "invalid"}},
                      {"base": {"ref": "develop", "repo": {"full_name": REPO}}}):
            self.assertEqual(assess(pr={**PR, **patch})["state"], "hold")
        self.assertTrue(mod.fingerprint(REPO, 55, COMMENT["path"], COMMENT["body"]).startswith(
            "security-remediation:v1:"))

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


    def test_unknown_security_state_blocks_ordinary_work(self):
        tasks = [{"id": "security", "priority_class": "high-security",
                  "state": "repair-failed"},
                 {"id": "feature", "priority_class": "ordinary", "state": "queued"}]
        decision = mod.next_assignable(
            tasks, active_agents=0, capacity_verified=True, security_gates_verified=True,
            included_credits_verified=True, paid_overages_enabled=False)
        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["task_id"], "security")

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

"""Negative path checks for dormant branch and review policy."""
import copy
import unittest

from scripts.approval_relay_policy import check_coding_pr, prepare_review, REQUIRED

A = "a" * 40
B = "b" * 40


def sample():
    task = {"repository": "ScottMDavistv/azure-text-to-video",
            "pinned_target_sha": A, "target_pr": 46, "coding_pr": 63,
            "approved_remediation_issue": 62, "previous_review_head_sha": B}
    target = {"repository": task["repository"], "number": 46, "head_sha": A,
              "head_branch": "security/claude-nf02-nf08-nov16",
              "base_branch": "main", "state": "open", "draft": True}
    coding = {"repository": task["repository"], "number": 63,
              "origin_sha": A, "origin_provenance": "verified-creation-event",
              "base_branch": "main", "state": "open", "draft": True,
              "integrated_in_target": True,
              "integration_provenance": "verified-git-ancestry",
              "integrated_head_sha": A}
    ci = [{"name": name, "head_sha": A, "status": "completed",
           "conclusion": "success", "event": "pull_request",
           "actor": "github-actions"} for name in sorted(REQUIRED)]
    return task, target, coding, ci


class BranchChecks(unittest.TestCase):
    def test_safe_retarget_is_only_proposal(self):
        t, p, c, _ = sample()
        result = check_coding_pr(t, p, c)
        self.assertEqual(result["action"], "propose_retarget")
        self.assertEqual(result["new_base"], p["head_branch"])

    def test_correct_base(self):
        t, p, c, _ = sample()
        c["base_branch"] = p["head_branch"]
        self.assertEqual(check_coding_pr(t, p, c)["action"], "aligned")

    def test_wrong_origin_blocks_even_if_base_correct(self):
        t, p, c, _ = sample()
        c["base_branch"] = p["head_branch"]
        c["origin_sha"] = B
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")

    def test_unverified_origin_blocks(self):
        t, p, c, _ = sample()
        c["origin_provenance"] = "self-reported"
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")

    def test_target_head_changed_blocks(self):
        t, p, c, _ = sample()
        p["head_sha"] = B
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")

    def test_wrong_repo_blocks(self):
        t, p, c, _ = sample()
        c["repository"] = "attacker/repo"
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")

    def test_nondraft_blocks(self):
        t, p, c, _ = sample()
        c["draft"] = False
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")


class ReReviewChecks(unittest.TestCase):
    def test_exact_head_verified_ci_prepares_only(self):
        t, p, c, ci = sample()
        result = prepare_review(t, p, c, ci)
        self.assertEqual(result["state"], "pending_owner_approval")
        self.assertFalse(result["dispatch_enabled"])
        self.assertEqual(result["head_sha"], A)

    def test_previous_review_same_head_blocks(self):
        t, p, c, ci = sample()
        t["previous_review_head_sha"] = A
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_not_integrated_blocks(self):
        t, p, c, ci = sample()
        c["integrated_in_target"] = False
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_untrusted_integration_provenance_blocks(self):
        t, p, c, ci = sample()
        c["integration_provenance"] = "comment-claimed"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_old_head_ci_blocks(self):
        t, p, c, ci = sample()
        ci[0]["head_sha"] = B
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_missing_required_ci_blocks(self):
        t, p, c, ci = sample()
        ci.pop()
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_failed_ci_blocks(self):
        t, p, c, ci = sample()
        ci[0]["conclusion"] = "failure"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_non_github_actor_blocks(self):
        t, p, c, ci = sample()
        ci[0]["actor"] = "user"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_closed_target_blocks(self):
        t, p, c, ci = sample()
        p["state"] = "closed"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")


if __name__ == "__main__":
    unittest.main()

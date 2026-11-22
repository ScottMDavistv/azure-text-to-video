"""Negative path checks for dormant branch and review policy."""
import copy
import unittest

from scripts.approval_relay_policy import (
    check_coding_pr, prepare_review as plan_review, REQUIRED, REPO, REPO_ID, WORKFLOWS,
)

A = "a" * 40
B = "b" * 40
MAIN = "c" * 40
MERGE = "d" * 40
BLOB = "e" * 40


def sample():
    task = {"repository": "ScottMDavistv/azure-text-to-video",
            "pinned_target_sha": A, "target_pr": 46, "coding_pr": 63,
            "approved_remediation_issue": 62, "previous_review_head_sha": B,
            "ci_policy_source": "trusted-protected-task-ledger",
            "applicable_required_workflows": ["Backend Python Tests", "Security Checks"]}
    target = {"repository": task["repository"], "number": 46, "head_sha": A,
              "head_branch": "security/claude-nf02-nf08-nov16",
              "base_branch": "main", "state": "open", "draft": True}
    coding = {"repository": task["repository"], "number": 63,
              "origin_sha": A, "origin_provenance": "verified-creation-event",
              "head_sha": B, "integrated_coding_head_sha": B,
              "integration_target_pr": 46,
              "base_branch": "main", "state": "open", "draft": True,
              "integrated_in_target": True,
              "integration_provenance": "verified-git-ancestry",
              "integrated_head_sha": A}
    ci = [{"id": 100 + i, "workflow_id": 200 + i, "run_attempt": 1,
           "name": name, "head_sha": A, "status": "completed", "conclusion": "success",
           "event": "pull_request", "actor": {"login": "user"},
           "repository": {"id": REPO_ID, "full_name": REPO},
           "head_repository": {"id": REPO_ID, "full_name": REPO},
           "head_branch": target["head_branch"],
           "path": ".github/workflows/" + WORKFLOWS[name],
           "pull_requests": [{"number": 46,
                              "head": {"ref": target["head_branch"], "sha": A,
                                       "repo": {"id": REPO_ID}},
                              "base": {"ref": "main", "sha": MAIN, "repo": {"id": REPO_ID}}}]}
          for i, name in enumerate(sorted(REQUIRED))]
    return task, target, coding, ci


def evidence(run, target):
    ref = (f'refs/pull/{target["number"]}/merge' if run["event"] == "pull_request"
           else f'refs/heads/{target["head_branch"]}')
    sha = MERGE if run["event"] == "pull_request" else target["head_sha"]
    return {"run": run,
            "workflow": {"id": run["workflow_id"],
                         "path": ".github/workflows/" + WORKFLOWS[run["name"]]},
            "execution": {"run_id": run["id"], "run_attempt": run["run_attempt"],
                          "workflow_sha": sha, "workflow_ref": f'{REPO}/{run["path"]}@{ref}',
                          "merge_parents": [MAIN, target["head_sha"]]},
            "protected_main_sha": MAIN,
            "workflow_files": {commit: {"mode": "100644", "blob_sha": BLOB}
                               for commit in (MAIN, target["head_sha"], sha)},
            "manual_approval": run.get("ledger_fixture")}


def prepare_review(task, target, coding, ci):
    # Stub models independent authoritative reads, not a trusted live adapter.
    return plan_review(task, target, coding, ci, lookup_workflow_evidence=lambda ident, path:
                       evidence(next(run for run in ci if run["id"] == ident), target))


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
        ci = [r for r in ci if r["name"] != "Security Checks"]
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_failed_ci_blocks(self):
        t, p, c, ci = sample()
        ci[0]["conclusion"] = "failure"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_actor_not_used_as_workflow_provenance(self):
        t, p, c, ci = sample()
        ci[0]["actor"] = "user"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "pending_owner_approval")

    def test_wrong_coding_pr_and_target_binding_block(self):
        for field, value in [("number", 999), ("integration_target_pr", 777),
                             ("integrated_coding_head_sha", A), ("head_sha", "short"),
                             ("repository", "attacker/repo")]:
            with self.subTest(field=field):
                t, p, c, ci = sample()
                c[field] = value
                self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_non_main_target_blocks(self):
        t, p, c, ci = sample()
        p["base_branch"] = "feature"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_wrong_or_unattested_workflow_blocks(self):
        for field, value in [("path", ".github/workflows/../backend-tests.yml"),
                             ("path", ".github/workflows/malicious.yml"),
                             ("repository", {"id": 999}), ("head_repository", {"id": 999}),
                             ("pull_requests", []), ("event", "push"), ("run_attempt", True)]:
            with self.subTest(field=field):
                t, p, c, ci = sample()
                ci[0][field] = value
                self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_path_filtered_applicable_checks(self):
        t, p, c, ci = sample()
        ci = [r for r in ci if r["name"] in t["applicable_required_workflows"]]
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "pending_owner_approval")
        t["ci_policy_source"] = "PR-comment"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_manual_dispatch_requires_attested_approval(self):
        t, p, c, ci = sample()
        ci[0]["event"] = "workflow_dispatch"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")
        ci[0]["manual_run_approved"] = True
        ci[0]["manual_approval_provenance"] = "trusted-protected-task-ledger"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")
        ci[0]["ledger_fixture"] = {"repository": REPO, "target_pr": p["number"],
                                  "head_sha": A, "workflow_id": ci[0]["workflow_id"],
                                  "run_id": ci[0]["id"], "run_attempt": ci[0]["run_attempt"]}
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "pending_owner_approval")
        ci[0]["ledger_fixture"]["run_attempt"] = 2
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_same_coding_pr_never_aligns_or_prepares(self):
        t, p, c, ci = sample()
        c["number"] = t["coding_pr"] = p["number"]
        c["base_branch"] = p["head_branch"]
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")
        c["base_branch"] = "main"
        self.assertEqual(check_coding_pr(t, p, c)["action"], "block")
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")
        for bad in (True, 0, "63"):
            t, p, c, ci = sample()
            c["number"] = t["coding_pr"] = bad
            self.assertEqual(check_coding_pr(t, p, c)["action"], "block")
            self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")

    def test_raw_snapshot_cannot_self_attest(self):
        t, p, c, ci = sample()
        for record in ci:
            record.update(trusted_workflow=True, provenance="verified-github-workflow-api")
        self.assertEqual(plan_review(t, p, c, ci)["reason"],
                         "trusted workflow evidence adapter unavailable")

    def test_candidate_or_execution_workflow_change_and_bad_git_metadata_block(self):
        t, p, c, ci = sample()
        pick = ci[0]["id"]
        for mutation in (
            lambda e: e["workflow_files"][A].update(blob_sha="f" * 40),
            lambda e: e["workflow_files"][MERGE].update(blob_sha="f" * 40),
            lambda e: e["workflow_files"][MAIN].update(mode="120000"),
            lambda e: e["workflow"].update(path=".github/workflows/not-allowed.yml"),
            lambda e: e["workflow"].update(id=9999999),
            lambda e: e["execution"].update(workflow_ref=f"{REPO}/.github/workflows/other.yml@refs/pull/46/merge"),
            lambda e: e["execution"].update(workflow_ref=f"{REPO}/.github/workflows/backend-tests.yml@refs/heads/main"),
            lambda e: e["execution"].update(merge_parents=[A, MAIN]),
            lambda e: e["execution"].update(run_attempt=3),
        ):
            def lookup(ident, path):
                entry = evidence(next(r for r in ci if r["id"] == ident), p)
                if ident == pick:
                    mutation(entry)
                return entry
            self.assertEqual(plan_review(t, p, c, ci, lookup_workflow_evidence=lookup)["state"], "blocked")

    def test_actual_pr_run_api_shape_requires_separate_source_evidence(self):
        # Sanitized REST fields from exact PR #58 run 44222311501.
        # Execution context/Git refs are synthetic, not an attestation of that old run.
        t, p, c, _ = sample()
        head = "5dd57b434942eec01de5f86ee41ec948b4d81d75"
        base = "18d436d0d7f23eb725e28d672e8208ba3e393563"
        p.update(number=58, head_sha=head, head_branch="docs/approval-gated-claude-relay-nov17")
        t.update(target_pr=58, applicable_required_workflows=["Backend Python Tests"])
        c.update(integration_target_pr=58, integrated_head_sha=head)
        row = {"id": 44222311501, "workflow_id": 419422801, "run_attempt": 1,
               "name": "Backend Python Tests", "status": "completed", "conclusion": "success",
               "path": ".github/workflows/backend-tests.yml", "event": "pull_request",
               "actor": {"login": "ScottMDavistv"}, "head_sha": head,
               "repository": {"id": REPO_ID, "full_name": REPO},
               "head_repository": {"id": REPO_ID, "full_name": REPO},
               "pull_requests": [{"number": 58,
                                  "head": {"ref": p["head_branch"], "sha": head, "repo": {"id": REPO_ID}},
                                  "base": {"ref": "main", "sha": base, "repo": {"id": REPO_ID}}}]}
        def lookup(ident, path):
            self.assertEqual((ident, path), (row["id"], row["path"]))
            entry = evidence(row, p)
            entry["execution"]["merge_parents"] = [base, head]
            entry["workflow_files"][base] = {"mode": "100644", "blob_sha": BLOB}
            return entry
        self.assertFalse(plan_review(t, p, c, [row], lookup_workflow_evidence=lookup)["dispatch_enabled"])
        self.assertEqual(plan_review(t, p, c, [row])["state"], "blocked")

    def test_closed_target_blocks(self):
        t, p, c, ci = sample()
        p["state"] = "closed"
        self.assertEqual(prepare_review(t, p, c, ci)["state"], "blocked")


if __name__ == "__main__":
    unittest.main()

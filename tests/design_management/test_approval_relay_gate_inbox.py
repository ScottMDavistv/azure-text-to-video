"""Regression tests: fail-closed origin CI and unified owner inbox."""
import copy
import unittest
from scripts.approval_relay_ci_gate import verify
from scripts.approval_relay_inbox import queue_item, corrective_task, consolidate

SHA = "a"*40


def attestation():
    return {"repository": "ScottMDavistv/azure-text-to-video",
            "coding_pr": 63, "target_pr": 46,
            "head_branch": "copilot/task", "base_branch": "security/claude-nf02-nf08-nov16",
            "target_branch": "security/claude-nf02-nf08-nov16", "target_head_sha": SHA,
            "origin_sha": SHA, "origin_verified": True, "merge_base_sha": SHA,
            "is_target_ancestor": True, "coding_head_sha": "b"*40, "draft": True,
            "source": "trusted-protected-task-ledger", "task_approved": True}


class AncestryGate(unittest.TestCase):
    def test_good(self):
        self.assertTrue(verify(attestation()))

    def test_fail_closed(self):
        mutations = (
            ("base_branch", "main"), ("origin_sha", "b"*40),
            ("origin_verified", False), ("source", "PR-comment"),
            ("merge_base_sha", "b"*40), ("is_target_ancestor", False),
            ("task_approved", False), ("draft", False),
            ("target_head_sha", "short"), ("repository", "attacker/repo"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                data = attestation()
                data[key] = value
                with self.assertRaises(ValueError):
                    verify(data)

    def test_missing_evidence_fails(self):
        data = attestation()
        del data["origin_verified"]
        with self.assertRaises(ValueError):
            verify(data)


class UnifiedQueue(unittest.TestCase):
    def test_three_decision_types_deduplicate(self):
        items = [queue_item("remediation", 46, SHA, "Fix", "Finding"),
                 queue_item("claude_review", 46, SHA, "Review", "Full diff"),
                 queue_item("security_merge", 46, SHA, "Merge decision", "Owner only")]
        self.assertEqual(len(consolidate(items + [copy.deepcopy(items[0])])), 3)
        self.assertTrue(all(x["status"] == "pending_owner_approval" for x in items))
        self.assertTrue(all(not x["dispatch_enabled"] for x in items))

    def test_corrective_task_is_deduplicated_and_not_dispatched(self):
        a = corrective_task(63, 46, SHA, "Wrong origin")
        b = corrective_task(63, 46, SHA, "Wrong origin")
        self.assertEqual(a["dedupe_marker"], b["dedupe_marker"])
        self.assertIn("Owner approval", a["issue_body"])
        self.assertEqual(consolidate([a,a]), [a])

    def test_reject_active_or_conflicting(self):
        x = queue_item("claude_review", 46, SHA, "Review", "Full diff")
        y = copy.deepcopy(x)
        y["dispatch_enabled"] = True
        with self.assertRaises(ValueError):
            consolidate([x,y])
        z = copy.deepcopy(x)
        z["details"] = "different instructions"
        with self.assertRaises(ValueError):
            consolidate([x,z])


if __name__ == "__main__":
    unittest.main()

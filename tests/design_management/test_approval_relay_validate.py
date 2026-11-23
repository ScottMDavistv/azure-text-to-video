"""Offline negative-path tests for inactive relay drafts."""
import copy
import unittest

from scripts.approval_relay_validate import validate_pending


def draft():
    return {
        "schema_version": 1,
        "handoff_id": "vrl-pr46-security-review-001",
        "kind": "security_review",
        "repository": "ScottMDavistv/azure-text-to-video",
        "pr_number": 46,
        "base_ref": "main",
        "head_sha": "73fe6d2a8aeeef97d34ba23c83fcce5e63f649cc",
        "target_agent": "claude",
        "scope": "entire PR diff against protected main",
        "instructions": "Independent exact-head full-diff review only.",
        "approval": {
            "state": "pending", "approved_by_user_id": None,
            "approved_payload_sha256": None, "approved_at": None,
            "expires_at": None, "nonce": None},
        "execution": {"state": "disabled", "provider": "manual-claude-cloud"},
    }


class PendingRelayValidation(unittest.TestCase):
    def test_valid_is_deterministic(self):
        self.assertEqual(validate_pending(draft()), validate_pending(draft()))
        self.assertEqual(len(validate_pending(draft())), 64)

    def test_different_head_changes_digest(self):
        a = draft()
        b = copy.deepcopy(a)
        b["head_sha"] = "a" * 40
        self.assertNotEqual(validate_pending(a), validate_pending(b))

    def test_reject_invalid_mutations(self):
        cases = [
            ("approval", {"state": "approved"}),
            ("execution", {"state": "enabled"}),
            ("head_sha", "deadbeef"),
            ("repository", "attacker/other"),
            ("base_ref", "some-branch"),
            ("target_agent", "attacker"),
            ("pr_number", True),
            ("instructions", ""),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                item = draft()
                if field in ("approval", "execution"):
                    item[field].update(value)
                else:
                    item[field] = value
                with self.assertRaises(ValueError):
                    validate_pending(item)

    def test_reject_unexpected_fields(self):
        item = draft()
        item["dispatch_now"] = True
        with self.assertRaises(ValueError):
            validate_pending(item)


if __name__ == "__main__":
    unittest.main()

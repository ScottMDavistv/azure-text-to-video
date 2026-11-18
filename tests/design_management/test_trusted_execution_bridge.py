"""Offline adversarial regression tests for the non-dispatch bridge prototype."""
import copy
import unittest

from scripts.trusted_execution_bridge import (
    capacity_policy, dispatch, identity, ordered_pending, parse_json, pending_job,
    validate_request,
)

H = "a" * 40


def request(**overrides):
    value = {
        "schema_version": 1,
        "repository": "ScottMDavistv/azure-text-to-video",
        "kind": "security_remediation",
        "pr_number": 55,
        "head_sha": H,
        "scope": "offline full-diff security remediation",
        "instructions": "tests only; no execution",
        "requested_agent": "claude",
    }
    value.update(overrides)
    return value


def snapshot(**overrides):
    value = {
        "active_agents": 1, "active_reviewers": 0,
        "capacity_complete": True, "credits_verified": True,
        "paid_overages_disabled": True, "provider_supported": True,
        "approval_trusted": True, "head_unchanged": True,
        "kill_switch_disabled": True,
    }
    value.update(overrides)
    return value


class BridgePolicyTests(unittest.TestCase):
    def test_pending_not_dispatchable(self):
        job = pending_job(request())
        self.assertEqual(job["state"], "pending_owner_approval")
        self.assertIs(job["dispatch_enabled"], False)
        with self.assertRaises(RuntimeError):
            dispatch(job)

    def test_dedupe_identical_jobs(self):
        job = pending_job(request())
        self.assertEqual(len(ordered_pending([job, copy.deepcopy(job)])), 1)

    def test_priority_security_before_feature(self):
        items = [pending_job(request(kind="feature", pr_number=3)),
                 pending_job(request(kind="security_review", pr_number=5)),
                 pending_job(request(kind="security_remediation", pr_number=9))]
        self.assertEqual([v["request"]["kind"] for v in ordered_pending(items)],
                         ["security_remediation", "security_review", "feature"])

    def test_modified_payload_hash_rejected(self):
        job = pending_job(request())
        job["request"]["instructions"] = "other scope"
        with self.assertRaises(ValueError):
            ordered_pending([job])

    def test_untrusted_state_rejected(self):
        job = pending_job(request())
        job["state"] = "approved"
        with self.assertRaises(ValueError):
            ordered_pending([job])

    def test_smuggled_dispatch_rejected(self):
        job = pending_job(request())
        job["dispatch_enabled"] = True
        with self.assertRaises(ValueError):
            ordered_pending([job])

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            validate_request(request(approval=True))

    def test_boolean_pr_number_rejected(self):
        with self.assertRaises(ValueError):
            validate_request(request(pr_number=True))

    def test_bad_sha_rejected(self):
        with self.assertRaises(ValueError):
            validate_request(request(head_sha="a" * 39))

    def test_bidi_and_newline_rejected(self):
        for text in ("attack\u202eexample", "attack\u2066example", "one\ntwo"):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    validate_request(request(instructions=text))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            parse_json('{"head_sha":"one","head_sha":"two"}')

    def test_nonstandard_json_rejected(self):
        with self.assertRaises(ValueError):
            parse_json('{"risk":NaN}')

    def test_payload_hash_changes_with_sha(self):
        self.assertNotEqual(identity(request()), identity(request(head_sha="b" * 40)))

    def test_missing_capacity_evidence_holds(self):
        self.assertEqual(capacity_policy({})["state"], "hold")

    def test_reviewers_consume_capacity(self):
        self.assertEqual(capacity_policy(snapshot(active_agents=1, active_reviewers=1))["state"], "hold")

    def test_boolean_agent_count_rejected(self):
        self.assertEqual(capacity_policy(snapshot(active_agents=True))["state"], "hold")

    def test_unknown_cost_blocks(self):
        self.assertEqual(capacity_policy(snapshot(credits_verified=None))["state"], "hold")

    def test_activation_not_inferred(self):
        self.assertEqual(capacity_policy(snapshot(provider_supported=False))["state"], "hold")

    def test_stale_head_blocks(self):
        self.assertEqual(capacity_policy(snapshot(head_unchanged=False))["state"], "hold")

    def test_emergency_stop_blocks(self):
        self.assertEqual(capacity_policy(snapshot(kill_switch_disabled=False))["state"], "hold")

    def test_positive_is_advisory_not_dispatch(self):
        result = capacity_policy(snapshot())
        self.assertEqual(result["state"], "eligible_for_manual_handoff")
        with self.assertRaises(RuntimeError):
            dispatch(result)


if __name__ == "__main__":
    unittest.main()

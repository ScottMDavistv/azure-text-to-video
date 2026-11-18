"""Discriminating mutation tests for the unified approval queue (offline).

Each mutant removes or weakens exactly one security control in
``scripts/approval_relay_queue.py``. The mutated source is compiled into a
throwaway in-memory module (nothing is written to disk) and the adversarial
queue suite is re-run against it; every mutant must be *killed* (at least one
failure or error). A surviving mutant means that control has no regression
test. Each mutation's anchor text must occur exactly once, so the harness
fails loudly instead of silently skipping when the module is edited.
"""
import importlib.util
import io
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "approval_relay_queue.py"
SUITE = Path(__file__).resolve().parent / "test_approval_relay_queue.py"
# Classes that exercise behaviour through ``q``. CLI/AST/workflow checks read
# the on-disk file and would not see the in-memory mutant.
SKIP_CLASSES = frozenset({"NoSideEffects", "CommandLine", "SharedPolicyRegression"})

MUTANTS = {
    # --- M-1: ledger certification must be owner-attested and digest bound ---
    "M1 certification attestation not verified": (
        "                verify_certification_attestation(\n",
        "                (lambda *a, **k: None)(\n"),
    "M1 certification digest not bound": (
        'extra_bindings=(("certification_sha256", sha256_hex(certification)),),',
        "extra_bindings=(),"),
    "M1 approval decision accepted as certification": (
        'decisions=("certify",)', 'decisions=("certify", "approve")'),
    "M1 certification scheme not domain separated": (
        "scheme=CERT_ATTESTATION_SCHEME,", 'scheme=att["scheme"],'),
    "M1 certification nonce not consumed": (
        'nonces.add(data["attestation"]["nonce"])\n                record["state"] = "certified"',
        'record["state"] = "certified"'),
    "M1 certification merge flag unchecked": (
        'cert["merge_enabled"] is not False or', ""),
    "M1 certification note unchecked": ('cert["note"] != CERT_NOTE or', ""),
    "M1 evidence digest format unchecked": (
        "if not isinstance(cert[field], str) or not DIGEST.fullmatch(cert[field]):",
        "if False:"),
    "M1 certification head unbound": (
        'type(cert["head_sha"]) is not str or cert["head_sha"] != record["head_sha"]):',
        "False):"),
    "M1 certification key set unchecked": (
        "if not isinstance(cert, dict) or set(cert) != CERTIFICATION_KEYS:",
        "if not isinstance(cert, dict):"),
    # --- L-1: certify validates the record itself ---
    "L1 certify skips validate_record": (
        "        validate_record(record, allowed_states=frozenset({APPROVED}))\n"
        "    except (ValueError, TypeError):\n        return no(\"invalid or synthetic",
        "        pass\n    except (ValueError, TypeError):\n        return no(\"invalid or synthetic"),
    # --- L-2: independence over the whole reviewed range ---
    "L2 commit contributors ignored": (
        'report["reviewer_session_id"] in reviewed_range["contributor_session_ids"]):',
        "False):"),
    "L2 stack implementers limited to one PR": (
        'for number in reviewed_range["pr_numbers"]:',
        'for number in [record["pr_number"]]:'),
    "L2 stacked implementer check removed": (
        'if (report["reviewer_session_id"] in stack_implementers or', "if (False or"),
    "L2 range source unchecked": (
        'if reviewed_range["source"] != RANGE_SOURCE or reviewed_range["repository"] != REPO:',
        "if False:"),
    "L2 range head unbound": (
        'if reviewed_range["head_sha"] != record["head_sha"]:', "if False:"),
    "L2 range need not contain record PR": (
        'len(prs) != len(set(prs)) or record["pr_number"] not in prs):',
        "len(prs) != len(set(prs))):"),
    "L2 reviewer record binding removed": ('reviewer[0]["record_id"] != record["id"] or', ""),
    # --- L-3: previously surviving ledger/attestation controls ---
    "L3 event hash unchecked": (
        'if not isinstance(event["event_hash"], str) or event["event_hash"] != _event_hash(event):',
        "if False:"),
    "L3 sequence unchecked": (
        'if type(event["seq"]) is not int or event["seq"] != index:', "if False:"),
    "L3 sequence bool accepted": ('if type(event["seq"]) is not int or event["seq"]',
                                  'if event["seq"]'),
    "L3 attestation type binding removed": (
        "if type(att[field]) is not type(value) or att[field] != value:",
        "if att[field] != value:"),
    "L3 short key accepted": ("len(key) < 32", "len(key) < 1"),
    "L3 prev hash unchecked": ('if event["prev_hash"] != prev:', "if False:"),
    # --- L-4: trusted time ---
    "L4 append time not bound to trusted clock": (
        "if type(at) is not int or at != trusted_now:", "if False:"),
    "L4 append without trusted clock": (
        '    if not _int(trusted_now, 0):\n        raise ValueError("append requires',
        '    if False:\n        raise ValueError("append requires'),
    "L4 future-dated events accepted": (
        'if trusted_now is not None and event["at"] > trusted_now:', "if False:"),
    # --- L-5 and informational ---
    "L5 unapproved merge hold dropped": (
        'holds.append({"record_id": rid, "reason": UNAPPROVED_MERGE_HOLD,',
        '(lambda hold: None)({"record_id": rid, "reason": UNAPPROVED_MERGE_HOLD,'),
    "L5 unapproved merge treated as routine": ('elif kind == "security_merge":', "elif False:"),
    "I1 unhashable event type/source": (
        "if (not isinstance(etype, str) or not isinstance(source, str) or",
        "if (False or"),
    "I1 unhashable record id": (
        'if not isinstance(rid, str) or not RECORD_ID.fullmatch(rid):', "if False:"),
    "I4 owner-only P0 reserves slot": ('r["agent_role"] != "owner" and\n', "True and\n"),
    "I5 target merge reported as closed": (
        'if kind == "branch_correction" and head_pr["merged"]:', "if False:"),
    # --- pre-existing controls (regression guard) ---
    "approver allowlist removed": (
        '            or att["approver_user_id"] not in approver_ids):', "            ):"),
    "expiry unchecked": ('if not att["issued_at"] <= now < att["expires_at"]:',
                         'if not att["issued_at"] <= now:'),
    "nonce replay accepted": ('if att["nonce"] in used_nonces:', "if False:"),
    "MAC unchecked": ('if not hmac.compare_digest(expected, att["signature"]):', "if False:"),
    "TTL unchecked": ('if att["expires_at"] - att["issued_at"] > max_ttl:', "if False:"),
    "payload digest unchecked": ('if record["payload_sha256"] != sha256_hex(_payload(record)):',
                                 "if False:"),
    "record identity unchecked": ('if record["id"] != _expected_id(',
                                  'if False and record["id"] != _expected_id('),
    "one live decision per slot removed": (
        'if live is not None and records[live]["state"] in LIVE_STATES:', "if False:"),
    "anchor unchecked": ("if anchor_hash is not None and anchor_hash != prev:", "if False:"),
    "dispatch flags unchecked": ('if (record["dispatch_enabled"] is not False or', "if (False or"),
    "reconciler may enqueue work": (
        'if source == "trusted-reconciler" and record["action_kind"] != "post_merge_reconciliation":',
        "if False:"),
    "blocking findings ignored": (
        'if f["severity"] in BLOCKING and f["status"] != "resolved":', "if False:"),
    "verdict unchecked": ('if report["verdict"] != "PASS":', "if False:"),
    "CI evidence ignored": ("    if ci_reason is not None:\n        return no(ci_reason)",
                            "    if False:\n        return no(ci_reason)"),
    "two-agent cap removed": ('elif cap["available"] < 1:', "elif False:"),
    "stale SHA not invalidated": ('if head_pr["head_sha"] != record["head_sha"]:', "if False:"),
    "merge completes at any head": (
        'pr["head_sha"] == record["head_sha"]):\n                proposals.append({"proposal": "complete"',
        'True):\n                proposals.append({"proposal": "complete"'),
    "owner-only merge admits agents": ('elif record["agent_role"] == "owner":', "elif False:"),
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compile(source):
    module = types.ModuleType("approval_relay_queue_mutant")
    module.__file__ = str(MODULE)
    exec(compile(source, "<approval_relay_queue mutant>", "exec"), module.__dict__)
    return module


class QueueMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MODULE.read_text(encoding="utf-8")
        cls.suite_module = _load("approval_relay_queue_suite_for_mutation", SUITE)
        cls.classes = sorted(
            name for name, obj in vars(cls.suite_module).items()
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
            and name not in SKIP_CLASSES)

    def run_against(self, module):
        suite_module = self.suite_module
        original = suite_module.q
        suite_module.q = module
        try:
            loader = unittest.TestLoader()
            tests = unittest.TestSuite(loader.loadTestsFromTestCase(getattr(suite_module, n))
                                       for n in self.classes)
            return unittest.TextTestRunner(stream=io.StringIO(), failfast=True,
                                           verbosity=0).run(tests)
        finally:
            suite_module.q = original

    def test_unmutated_module_passes_harness(self):
        result = self.run_against(_compile(self.source))
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertGreater(result.testsRun, 60)

    def test_every_security_mutant_is_killed(self):
        survivors = []
        for name, (old, new) in sorted(MUTANTS.items()):
            with self.subTest(mutant=name):
                self.assertEqual(self.source.count(old), 1, "mutation anchor not unique: " + name)
                result = self.run_against(_compile(self.source.replace(old, new)))
                if result.wasSuccessful():
                    survivors.append(name)
        self.assertEqual(survivors, [], "surviving mutants (untested controls)")


if __name__ == "__main__":
    unittest.main()

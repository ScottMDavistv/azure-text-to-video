"""Offline adversarial Stage 1/2 regressions; no live provider calls."""
import hashlib
import hmac
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.trusted_github_evidence import (
    EvidenceError, GitHubReadOnly, WorkflowRule, safe_path, strict_json,
    verify_pr_snapshot, verify_webhook, verify_workflow_provenance,
)
from scripts.trusted_agent_slots import Slots, SlotError

HEAD = "a" * 40
MAIN = "b" * 40
BLOB = "c" * 40
REPO = "ScottMDavistv/azure-text-to-video"
FILE = ".github/workflows/security.yml"


class FakeAPI:
    def __init__(self, changed=False, spoofed=False, truncated=False):
        self.changed, self.spoofed, self.truncated = changed, spoofed, truncated

    def get(self, path):
        if path.endswith("/pulls/81"):
            return ({"number": 81, "state": "open", "draft": True, "merged_at": None,
                     "head": {"sha": HEAD, "repo": {"full_name": REPO}},
                     "base": {"sha": MAIN, "ref": "main",
                              "repo": {"full_name": REPO}}}, {})
        if path.endswith("/git/ref/heads/main"):
            return ({"object": {"sha": MAIN}}, {})
        if "/contents/" in path:
            is_head = "ref=" + HEAD in path
            return ({"sha": "d" * 40 if is_head and self.changed else BLOB,
                     "type": "file"}, {})
        raise AssertionError(path)

    def pages(self, path):
        if self.truncated:
            raise EvidenceError("pagination incomplete")
        return [{"id": 42, "name": "Security Checks",
                 "path": ".github/workflows/other.yml" if self.spoofed else FILE,
                 "event": "pull_request", "status": "completed",
                 "conclusion": "success",
                 "pull_requests": [{"number": 81, "head": {"sha": HEAD}}]}]


class EvidenceTests(unittest.TestCase):
    def test_webhook_signature(self):
        body, secret = b'{"event":"pull_request"}', b"x" * 32
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        delivery = "12345678-1234-1234-1234-123456789abc"
        self.assertEqual(verify_webhook(body, sig, secret, delivery), delivery)
        with self.assertRaises(EvidenceError):
            verify_webhook(body, "sha256=" + "0" * 64, secret, delivery)

    def test_workflow_paths_fail_closed(self):
        for path in ("../.github/workflows/a.yml", ".github/workflows/../a.yml",
                     "/.github/workflows/a.yml", ".github/workflows/a.yml?ref=main",
                     "docs/workflow.yml"):
            with self.subTest(path=path), self.assertRaises(EvidenceError):
                safe_path(path)

    def test_reject_ambiguous_api_json(self):
        for raw in (b'{"number":81,"number":82}',
                    b'{"head":{"sha":"first","sha":"last"}}',
                    b'{"score":NaN}', b'{"score":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(EvidenceError):
                strict_json(raw)

    def test_refuse_foreign_pr_repository(self):
        class Foreign(FakeAPI):
            def get(self, path):
                data, headers = super().get(path)
                if path.endswith("/pulls/81"):
                    data["head"]["repo"]["full_name"] = "untrusted/other"
                return data, headers
        with self.assertRaises(EvidenceError):
            verify_pr_snapshot(Foreign(), 81, HEAD, MAIN)

    def test_exact_head(self):
        self.assertEqual(verify_pr_snapshot(FakeAPI(), 81, HEAD, MAIN)["head_sha"], HEAD)
        with self.assertRaises(EvidenceError):
            verify_pr_snapshot(FakeAPI(), 81, "d" * 40, MAIN)

    def test_trusted_ci(self):
        result = verify_workflow_provenance(
            FakeAPI(), 81, HEAD, MAIN, [WorkflowRule("Security Checks", FILE, BLOB)])
        self.assertEqual(result["state"], "ci_provenance_verified")

    def test_spoofed_name(self):
        with self.assertRaises(EvidenceError):
            verify_workflow_provenance(FakeAPI(spoofed=True), 81, HEAD, MAIN,
                                       [WorkflowRule("Security Checks", FILE, BLOB)])

    def test_modified_workflow(self):
        with self.assertRaises(EvidenceError):
            verify_workflow_provenance(FakeAPI(changed=True), 81, HEAD, MAIN,
                                       [WorkflowRule("Security Checks", FILE, BLOB)])

    def test_truncated_pages(self):
        with self.assertRaises(EvidenceError):
            verify_workflow_provenance(FakeAPI(truncated=True), 81, HEAD, MAIN,
                                       [WorkflowRule("Security Checks", FILE, BLOB)])

    def test_requires_authentication_token(self):
        with self.assertRaises(EvidenceError):
            GitHubReadOnly("")

    def test_read_only_repo_scoping(self):
        client = GitHubReadOnly("x" * 40)
        for path in ("https://evil.test/repos/x", "/users/123",
                     f"/repos/{REPO}/../other", f"/repos/{REPO}/issues%2f1"):
            with self.subTest(path=path), self.assertRaises(EvidenceError):
                client.get(path)


class SlotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = str(Path(folder.name) / "leases.db")
        self.slots = Slots(self.path)

    def test_memory_is_not_durable(self):
        with self.assertRaises(SlotError):
            Slots(":memory:")

    def test_two_slots_include_reviewer(self):
        self.slots.acquire("job-1", "claude", "coder", now=100)
        self.slots.acquire("job-2", "claude", "reviewer", now=100)
        with self.assertRaises(SlotError):
            self.slots.acquire("job-3", "copilot", "coder", now=100)

    def test_survives_separate_instances(self):
        self.slots.acquire("job-1", "claude", "coder", now=100)
        self.assertEqual(len(Slots(self.path).snapshot()), 1)

    def test_three_threads_compete_atomically(self):
        barrier, lock = threading.Barrier(3), threading.Lock()
        passed, rejected = [], []
        def work(i):
            barrier.wait()
            try:
                lease = Slots(self.path).acquire(f"job-{i}", "claude", "coder", now=100)
                with lock:
                    passed.append(lease)
            except SlotError:
                with lock:
                    rejected.append(i)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual((len(passed), len(rejected)), (2, 1))

    def test_expired_agent_does_not_release_capacity(self):
        one = self.slots.acquire("job-1", "claude", "coder", now=100, ttl=10)
        self.slots.acquire("job-2", "claude", "reviewer", now=100, ttl=10)
        self.slots.quarantine_expired(one, now=111)
        with self.assertRaises(SlotError):
            self.slots.acquire("job-3", "claude", "coder", now=500)

    def test_released_slot_has_new_fencing_token(self):
        one = self.slots.acquire("job-1", "claude", "coder", now=100)
        with self.assertRaises(SlotError):
            self.slots.release(one, now=110)
        self.slots.release(one, termination_verified=True, now=110)
        two = self.slots.acquire("job-1", "claude", "coder", now=120)
        self.assertEqual(two["generation"], 2)
        with self.assertRaises(SlotError):
            self.slots.heartbeat(one, now=121)
        self.assertGreaterEqual(len(self.slots.audit()), 3)

    def test_duplicate_active_job_rejected(self):
        self.slots.acquire("same", "claude", "coder", now=100)
        with self.assertRaises(SlotError):
            self.slots.acquire("same", "claude", "coder", now=101)

    def test_rejects_unknown_agent_role(self):
        with self.assertRaises(SlotError):
            self.slots.acquire("job", "claude", "untrusted", now=100)


if __name__ == "__main__":
    unittest.main()

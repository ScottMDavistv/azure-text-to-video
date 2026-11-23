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


REPO_EVIDENCE = {"id": 1457187422, "full_name": REPO}


def ci_run(**changes):
    run = {"id": 42, "name": "Security Checks", "path": FILE,
           "event": "pull_request", "status": "completed", "conclusion": "success",
           "head_sha": HEAD, "repository": REPO_EVIDENCE,
           "head_repository": REPO_EVIDENCE, "workflow_id": 8,
           "run_number": 10, "run_attempt": 1,
           "pull_requests": [{"number": 81, "head": {"sha": HEAD}}]}
    return dict(run, **changes)


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
        if path.endswith("/actions/workflows/8"):
            return {"id": 8, "name": "Security Checks", "path": FILE}, {}
        raise AssertionError(path)

    def pages(self, path, *, collection=None):
        if self.truncated:
            raise EvidenceError("pagination incomplete")
        return [ci_run(path=".github/workflows/other.yml" if self.spoofed else FILE)]


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


    def test_exact_ci_result_never_uses_foreign_or_older_success(self):
        class Runs(FakeAPI):
            def pages(self, _path, *, collection=None):
                self.assert_collection = collection
                return self.rows

        api = Runs()
        rule = [WorkflowRule("Security Checks", FILE, BLOB)]
        success = ci_run()
        failed = ci_run(id=43, run_number=11, conclusion="failure")
        retry = ci_run(run_attempt=2, status="in_progress", conclusion=None)
        for rows in (
            [ci_run(head_sha="e" * 40)],
            [ci_run(repository={"id": 8, "full_name": REPO})],
            [ci_run(head_repository={"id": 1457187422, "full_name": "foreign/repo"})],
            [ci_run(pull_requests=[])],
            [ci_run(run_attempt=True)],
            [failed, success],
            [success, retry],
            [success, success],
            [success, ci_run(workflow_id=9, id=44, run_number=9)],
        ):
            with self.subTest(rows=rows), self.assertRaises(EvidenceError):
                api.rows = rows
                verify_workflow_provenance(api, 81, HEAD, MAIN, rule)
        api.rows = [ci_run(id=43, run_number=11, run_attempt=2), failed, success]
        result = verify_workflow_provenance(api, 81, HEAD, MAIN, rule)
        self.assertEqual(result["checks"]["Security Checks"]["run_ids"], [43])
        self.assertEqual(result["checks"]["Security Checks"]["run_attempt"], 2)
        self.assertIs(result["execution_authority"], False)

    def test_actions_wrapped_pagination_is_bound_and_complete(self):
        class Local(GitHubReadOnly):
            def get(self, path):
                page = path.endswith("&page=2")
                return ({"total_count": 2, "workflow_runs": [2] if page else [1]},
                        {} if page else self.headers)

        api = Local("inert-test-token-only-no-network")
        path = f"/repos/{REPO}/actions/runs?event=pull_request&head_sha={HEAD}&per_page=100"
        link = f'<https://api.github.com{path}&page=2>; rel="next"'
        api.headers = {"link": link}
        self.assertEqual(api.pages(path, collection="workflow_runs"), [1, 2])
        for headers in ({}, {"link": link.replace(HEAD, MAIN)},
                        {"link": link.replace("page=2", "page=3")}):
            with self.subTest(headers=headers), self.assertRaises(EvidenceError):
                api.headers = headers
                api.pages(path, collection="workflow_runs")

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
    def at(self, tick, operation, *args, **kwargs):
        self.tick = tick
        return operation(*args, **kwargs)

    def clock(self):
        return self.tick

    def setUp(self):
        self.tick = 100
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = str(Path(folder.name) / "leases.db")
        self.slots = Slots(self.path, clock=self.clock)

    def test_operation_cannot_forge_lease_time(self):
        lease = self.at(100, self.slots.acquire, "job-1", "claude", "coder", ttl=10)
        self.tick = 111
        with self.assertRaises(TypeError):
            self.slots.heartbeat(lease, now=100)
        with self.assertRaises(SlotError):
            self.slots.heartbeat(lease)

    def test_memory_is_not_durable(self):
        with self.assertRaises(SlotError):
            Slots(":memory:")

    def test_two_slots_include_reviewer(self):
        self.at(100, self.slots.acquire, 'job-1', 'claude', 'coder')
        self.at(100, self.slots.acquire, 'job-2', 'claude', 'reviewer')
        with self.assertRaises(SlotError):
            self.at(100, self.slots.acquire, 'job-3', 'copilot', 'coder')

    def test_survives_separate_instances(self):
        self.at(100, self.slots.acquire, 'job-1', 'claude', 'coder')
        self.assertEqual(len(Slots(self.path, clock=self.clock).snapshot()), 1)

    def test_three_threads_compete_atomically(self):
        barrier, lock = threading.Barrier(3), threading.Lock()
        passed, rejected = [], []
        def work(i):
            barrier.wait()
            try:
                lease = self.at(100, Slots(self.path, clock=self.clock).acquire, f'job-{i}', 'claude', 'coder')
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
        one = self.at(100, self.slots.acquire, 'job-1', 'claude', 'coder', ttl=10)
        self.at(100, self.slots.acquire, 'job-2', 'claude', 'reviewer', ttl=10)
        self.at(111, self.slots.quarantine_expired, one)
        with self.assertRaises(SlotError):
            self.at(500, self.slots.acquire, 'job-3', 'claude', 'coder')

    def test_released_slot_has_new_fencing_token(self):
        one = self.at(100, self.slots.acquire, 'job-1', 'claude', 'coder')
        with self.assertRaises(SlotError):
            self.at(110, self.slots.release, one)
        self.at(110, self.slots.release, one, termination_verified=True)
        two = self.at(120, self.slots.acquire, 'job-1', 'claude', 'coder')
        self.assertEqual(two["generation"], 2)
        with self.assertRaises(SlotError):
            self.at(121, self.slots.heartbeat, one)
        self.assertGreaterEqual(len(self.slots.audit()), 3)

    def test_duplicate_active_job_rejected(self):
        self.at(100, self.slots.acquire, 'same', 'claude', 'coder')
        with self.assertRaises(SlotError):
            self.at(101, self.slots.acquire, 'same', 'claude', 'coder')

    def test_rejects_unknown_agent_role(self):
        with self.assertRaises(SlotError):
            self.at(100, self.slots.acquire, 'job', 'claude', 'untrusted')


if __name__ == "__main__":
    unittest.main()

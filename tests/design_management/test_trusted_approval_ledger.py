"""Offline Stage 1B authorization and replay adversarial tests."""
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.trusted_approval_ledger import (
    ApprovalError, ApprovalLedger, GitHubUserIdentity, GitHubHeadVerifier, dispatch
)

SHA = "a"*40
OWNER = 212811615
TOKEN = "test-only-github-user-access-token"
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def request(**extra):
    value = {"schema_version": 1, "repository": "ScottMDavistv/azure-text-to-video",
             "kind": "security_remediation", "pr_number": 81, "head_sha": SHA,
             "scope": "offline only", "instructions": "no dispatch",
             "requested_agent": "claude"}
    value.update(extra)
    return value


class Identity:
    def __init__(self, uid=OWNER):
        self.uid = uid
    def owner_id(self, token):
        if token != TOKEN:
            raise ApprovalError("untrusted user token")
        return self.uid


class Head:
    def __init__(self, sha=SHA):
        self.sha = sha
    def assert_current(self, pr, sha):
        if pr != 81 or sha != self.sha:
            raise ApprovalError("head changed")


class Response:
    status = 200
    def __init__(self, value):
        self.raw = json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, n):
        return self.raw[:n]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "approvals.db")
        self.heads = Head()
        self.ledger = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                                     identity_provider=Identity(),
                                     head_verifier=self.heads)

    def approve(self, proposal, **changes):
        params = dict(job_id=proposal["job_id"], payload_digest=proposal["payload_digest"],
                      challenge=proposal["challenge"], user_access_token=TOKEN, now=200)
        params.update(changes)
        return self.ledger.approve(**params)

    def test_auth_user_numeric_id_from_verified_endpoint(self):
        def opener(req, timeout):
            self.assertEqual(req.full_url, "https://api.github.com/user")
            self.assertEqual(req.get_method(), "GET")
            self.assertTrue(req.get_header("Authorization").startswith("Bearer "))
            return Response({"id": OWNER, "login": "untrusted-login"})
        self.assertEqual(GitHubUserIdentity(opener=opener).owner_id(TOKEN), OWNER)
        for value in ({"id": True}, {"id": "212811615"}, {"login": "ScottMDavistv"}):
            with self.assertRaises(ApprovalError):
                GitHubUserIdentity(opener=lambda req, timeout: Response(value)).owner_id(TOKEN)

    def test_trusted_head_verifier(self):
        class API:
            def __init__(self, sha=SHA):
                self.sha = sha
            def get(self, path):
                self_path = "/repos/ScottMDavistv/azure-text-to-video/pulls/81"
                self_assert = path == self_path
                if not self_assert:
                    raise AssertionError(path)
                return ({"number": 81, "state": "open", "draft": True,
                         "merged_at": None, "head": {"sha": self.sha},
                         "base": {"ref": "main"}}, {})
        GitHubHeadVerifier(API()).assert_current(81, SHA)
        with self.assertRaises(ApprovalError):
            GitHubHeadVerifier(API("b"*40)).assert_current(81, SHA)

    def test_untrusted_head_provider_missing(self):
        with self.assertRaises(ApprovalError):
            ApprovalLedger(self.path, authorized_owner_id=OWNER)

    def test_valid_owner_approval_is_not_dispatch(self):
        p = self.ledger.propose(request(), now=100)
        self.assertFalse(p["dispatch_enabled"])
        approved = self.approve(p)
        self.assertEqual(approved["state"], "approved_not_dispatched")
        self.assertFalse(approved["dispatch_enabled"])
        with self.assertRaises(RuntimeError):
            dispatch(approved)

    def test_wrong_authenticated_user_rejected(self):
        p = self.ledger.propose(request(), now=100)
        other = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                               identity_provider=Identity(999), head_verifier=Head())
        with self.assertRaises(ApprovalError):
            other.approve(job_id=p["job_id"], payload_digest=p["payload_digest"],
                          challenge=p["challenge"], user_access_token=TOKEN, now=200)

    def test_unverified_token_rejected(self):
        p = self.ledger.propose(request(), now=100)
        with self.assertRaises(ApprovalError):
            self.approve(p, user_access_token="forged")

    def test_stale_head_rejected(self):
        p = self.ledger.propose(request(), now=100)
        self.heads.sha = "b"*40
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_nonce_single_use(self):
        p = self.ledger.propose(request(), now=100)
        self.approve(p)
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_modified_payload_rejected(self):
        p = self.ledger.propose(request(), now=100)
        with self.assertRaises(ApprovalError):
            self.approve(p, payload_digest="f"*64)

    def test_wrong_nonce_rejected(self):
        p = self.ledger.propose(request(), now=100)
        with self.assertRaises(ApprovalError):
            self.approve(p, challenge="n"*50)

    def test_ttl_limits_and_expiration(self):
        for ttl in (True, 0, 59, 3601):
            with self.subTest(ttl=ttl), self.assertRaises(ApprovalError):
                self.ledger.propose(request(), now=100, ttl=ttl)
        p = self.ledger.propose(request(), now=100, ttl=60)
        with self.assertRaises(ApprovalError):
            self.approve(p, now=160)

    def test_no_silent_reissue_same_payload(self):
        self.ledger.propose(request(), now=100)
        with self.assertRaises(ApprovalError):
            self.ledger.propose(request(), now=200)

    def test_approved_record_survives_reopen_and_expires(self):
        p = self.ledger.propose(request(), now=100)
        self.approve(p)
        reopened = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                                  identity_provider=Identity(), head_verifier=Head())
        self.assertEqual(reopened.status(p["job_id"], now=200)["state"], "approved")
        self.assertEqual(reopened.status(p["job_id"], now=700)["state"], "expired_hold")

    def test_owner_revocation(self):
        p = self.ledger.propose(request(), now=100)
        with self.assertRaises(ApprovalError):
            self.ledger.revoke(p["job_id"], user_access_token="bad")
        self.ledger.revoke(p["job_id"], user_access_token=TOKEN, now=150)
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_append_only_audit(self):
        p = self.ledger.propose(request(), now=100)
        self.approve(p)
        self.assertEqual(len(self.ledger.audit_events()), 2)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM audit")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE audit SET event='forged'")

    def test_signed_webhook_single_delivery(self):
        body, secret = b'{"action":"opened"}', b"s"*32
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        result = self.ledger.record_verified_webhook(body=body, signature=sig,
                    secret=secret, delivery_id=DELIVERY, now=100)
        self.assertFalse(result["owner_approved"])
        with self.assertRaises(ApprovalError):
            self.ledger.record_verified_webhook(body=body, signature=sig,
                    secret=secret, delivery_id=DELIVERY, now=101)

    def test_webhook_forgery_does_not_poison_delivery(self):
        body, secret = b"payload", b"s"*32
        with self.assertRaises(ApprovalError):
            self.ledger.record_verified_webhook(body=body, signature="sha256="+"0"*64,
                secret=secret, delivery_id=DELIVERY, now=100)
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        self.ledger.record_verified_webhook(body=body, signature=sig,
            secret=secret, delivery_id=DELIVERY, now=100)

    def test_two_concurrent_approvals_only_one_wins(self):
        p = self.ledger.propose(request(), now=100)
        barrier, result, lock = threading.Barrier(2), [], threading.Lock()
        def worker():
            barrier.wait()
            try:
                self.approve(p)
                answer = "approved"
            except ApprovalError:
                answer = "blocked"
            with lock:
                result.append(answer)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(result), ["approved", "blocked"])


if __name__ == "__main__":
    unittest.main()

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
    def at(self, tick, operation, *args, **kwargs):
        self.tick = tick
        return operation(*args, **kwargs)

    def clock(self):
        return self.tick

    def setUp(self):
        self.tick = 100
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "approvals.db")
        self.heads = Head()
        self.ledger = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                                     identity_provider=Identity(),
                                     head_verifier=self.heads, clock=self.clock)

    def approve(self, proposal, **changes):
        params = dict(job_id=proposal["job_id"], payload_digest=proposal["payload_digest"],
                      challenge=proposal["challenge"], user_access_token=TOKEN)
        tick = changes.pop("now", 200)
        params.update(changes)
        return self.at(tick, self.ledger.approve, **params)

    def test_operation_cannot_forge_time_or_finish_after_expiry(self):
        proposal = self.at(100, self.ledger.propose, request(), ttl=60)
        params = dict(job_id=proposal["job_id"], payload_digest=proposal["payload_digest"],
                      challenge=proposal["challenge"], user_access_token=TOKEN)
        self.tick = 160
        with self.assertRaises(TypeError):
            self.ledger.approve(**params, now=0)
        with self.assertRaises(ApprovalError):
            self.ledger.approve(**params)
        self.tick = 159
        self.heads.assert_current = lambda *_: setattr(self, "tick", 160)
        with self.assertRaises(ApprovalError):
            self.ledger.approve(**params)
        self.assertEqual(self.ledger.status(proposal["job_id"])["state"], "expired_hold")

    def test_repeated_body_is_flagged_without_execution_authority(self):
        body = b'{"event":"pull_request"}'
        secret = b"k" * 32
        signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        args = dict(body=body, signature=signature, secret=secret)
        first = self.ledger.record_verified_webhook(**args, delivery_id=DELIVERY)
        second = self.ledger.record_verified_webhook(**args, delivery_id="02345678-1234-1234-1234-123456789abc")
        self.assertIs(first["body_seen_before"], False)
        self.assertIs(second["body_seen_before"], True)
        self.assertIs(second["execution_authority"], False)
        self.assertIs(second["owner_approved"], False)
        with self.assertRaises(ApprovalError):
            self.ledger.record_verified_webhook(**args, delivery_id=DELIVERY)

    def test_ambiguous_user_id_response_rejected(self):
        response = Response({"id": OWNER})
        response.raw = ('{"id":1,"id":%d}' % OWNER).encode()
        with self.assertRaises(ApprovalError):
            GitHubUserIdentity(opener=lambda req, timeout: response).owner_id(TOKEN)

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
                         "merged_at": None,
                         "head": {"sha": self.sha,
                                  "repo": {"full_name": "ScottMDavistv/azure-text-to-video"}},
                         "base": {"ref": "main",
                                  "repo": {"full_name": "ScottMDavistv/azure-text-to-video"}}}, {})
        GitHubHeadVerifier(API()).assert_current(81, SHA)
        with self.assertRaises(ApprovalError):
            GitHubHeadVerifier(API("b"*40)).assert_current(81, SHA)
        class Foreign(API):
            def get(self, path):
                result, headers = super().get(path)
                result["head"]["repo"]["full_name"] = "attacker/fork"
                return result, headers
        with self.assertRaises(ApprovalError):
            GitHubHeadVerifier(Foreign()).assert_current(81, SHA)

    def test_untrusted_head_provider_missing(self):
        with self.assertRaises(ApprovalError):
            ApprovalLedger(self.path, authorized_owner_id=OWNER)

    def test_valid_owner_approval_is_not_dispatch(self):
        p = self.at(100, self.ledger.propose, request())
        self.assertFalse(p["dispatch_enabled"])
        approved = self.approve(p)
        self.assertEqual(approved["state"], "approved_not_dispatched")
        self.assertFalse(approved["dispatch_enabled"])
        with self.assertRaises(RuntimeError):
            dispatch(approved)

    def test_wrong_authenticated_user_rejected(self):
        p = self.at(100, self.ledger.propose, request())
        other = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                               identity_provider=Identity(999), head_verifier=Head(), clock=self.clock)
        with self.assertRaises(ApprovalError):
            self.at(200, other.approve, job_id=p['job_id'], payload_digest=p['payload_digest'], challenge=p['challenge'], user_access_token=TOKEN)

    def test_unverified_token_rejected(self):
        p = self.at(100, self.ledger.propose, request())
        with self.assertRaises(ApprovalError):
            self.approve(p, user_access_token="forged")

    def test_stale_head_rejected(self):
        p = self.at(100, self.ledger.propose, request())
        self.heads.sha = "b"*40
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_nonce_single_use(self):
        p = self.at(100, self.ledger.propose, request())
        self.approve(p)
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_modified_payload_rejected(self):
        p = self.at(100, self.ledger.propose, request())
        with self.assertRaises(ApprovalError):
            self.approve(p, payload_digest="f"*64)

    def test_wrong_nonce_rejected(self):
        p = self.at(100, self.ledger.propose, request())
        with self.assertRaises(ApprovalError):
            self.approve(p, challenge="n"*50)

    def test_ttl_limits_and_expiration(self):
        for ttl in (True, 0, 59, 3601):
            with self.subTest(ttl=ttl), self.assertRaises(ApprovalError):
                self.at(100, self.ledger.propose, request(), ttl=ttl)
        p = self.at(100, self.ledger.propose, request(), ttl=60)
        with self.assertRaises(ApprovalError):
            self.approve(p, now=160)

    def test_no_silent_reissue_same_payload(self):
        self.at(100, self.ledger.propose, request())
        with self.assertRaises(ApprovalError):
            self.at(200, self.ledger.propose, request())

    def test_approved_record_survives_reopen_and_expires(self):
        p = self.at(100, self.ledger.propose, request())
        self.approve(p)
        reopened = ApprovalLedger(self.path, authorized_owner_id=OWNER,
                                  identity_provider=Identity(), head_verifier=Head(), clock=self.clock)
        self.assertEqual(self.at(200, reopened.status, p['job_id'])["state"], "approved")
        self.assertEqual(self.at(700, reopened.status, p['job_id'])["state"], "expired_hold")

    def test_owner_revocation(self):
        p = self.at(100, self.ledger.propose, request())
        with self.assertRaises(ApprovalError):
            self.ledger.revoke(p["job_id"], user_access_token="bad")
        self.at(150, self.ledger.revoke, p['job_id'], user_access_token=TOKEN)
        with self.assertRaises(ApprovalError):
            self.approve(p)

    def test_append_only_audit(self):
        p = self.at(100, self.ledger.propose, request())
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
        result = self.at(100, self.ledger.record_verified_webhook, body=body, signature=sig, secret=secret, delivery_id=DELIVERY)
        self.assertFalse(result["owner_approved"])
        with self.assertRaises(ApprovalError):
            self.at(101, self.ledger.record_verified_webhook, body=body, signature=sig, secret=secret, delivery_id=DELIVERY)

    def test_webhook_forgery_does_not_poison_delivery(self):
        body, secret = b"payload", b"s"*32
        with self.assertRaises(ApprovalError):
            self.at(100, self.ledger.record_verified_webhook, body=body, signature='sha256=' + '0' * 64, secret=secret, delivery_id=DELIVERY)
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        self.at(100, self.ledger.record_verified_webhook, body=body, signature=sig, secret=secret, delivery_id=DELIVERY)

    def test_two_concurrent_approvals_only_one_wins(self):
        p = self.at(100, self.ledger.propose, request())
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

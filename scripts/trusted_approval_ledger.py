"""Stage 1B: offline owner approval + replay ledger; no dispatch capability.

A trusted service must configure the numeric owner ID, GitHub user identity
client and authenticated GitHub PR-head verifier. Caller-supplied identity or
head assertions never authorize approval. Does not install or launch an app.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from scripts.trusted_execution_bridge import identity, validate_request
from scripts.trusted_github_evidence import GitHubReadOnly, verify_webhook, EvidenceError

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")


class ApprovalError(ValueError):
    pass


class GitHubUserIdentity:
    """Get authenticated numeric user ID from a GitHub user-access token."""
    def __init__(self, opener=urlopen):
        self._opener = opener

    def owner_id(self, token):
        if not isinstance(token, str) or len(token) < 20:
            raise ApprovalError("missing GitHub user authentication")
        request = Request("https://api.github.com/user", method="GET", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "video-render-approval-ledger/0.1",
        })
        try:
            with self._opener(request, timeout=15) as response:
                raw = response.read(65537)
                if response.status != 200 or len(raw) > 65536:
                    raise ApprovalError("authenticated identity unavailable")
            user = json.loads(raw)
        except (HTTPError, URLError, OSError, ValueError, UnicodeDecodeError) as exc:
            raise ApprovalError("authenticated identity unavailable") from exc
        if type(user) is not dict or type(user.get("id")) is not int or user["id"] < 1:
            raise ApprovalError("invalid authenticated numeric user ID")
        return user["id"]


class GitHubHeadVerifier:
    """Read live authenticated PR head; do not accept caller asserted SHA."""
    def __init__(self, client: GitHubReadOnly):
        self._client = client

    def assert_current(self, pr_number, expected_sha):
        if type(pr_number) is not int or pr_number < 1 or not isinstance(
                expected_sha, str) or not HEAD.fullmatch(expected_sha):
            raise ApprovalError("invalid PR identity")
        try:
            pr, _ = self._client.get(
                f"/repos/ScottMDavistv/azure-text-to-video/pulls/{pr_number}")
        except (EvidenceError, ValueError) as exc:
            raise ApprovalError("unavailable authenticated PR evidence") from exc
        if (type(pr) is not dict or pr.get("number") != pr_number or
                pr.get("state") != "open" or pr.get("draft") is not True or
                pr.get("merged_at") is not None or
                type(pr.get("head")) is not dict or
                pr["head"].get("sha") != expected_sha or
                type(pr["head"].get("repo")) is not dict or
                pr["head"]["repo"].get("full_name") !=
                    "ScottMDavistv/azure-text-to-video" or
                type(pr.get("base")) is not dict or
                pr["base"].get("ref") != "main" or
                type(pr["base"].get("repo")) is not dict or
                pr["base"]["repo"].get("full_name") !=
                    "ScottMDavistv/azure-text-to-video"):
            raise ApprovalError("current PR head/state mismatch")


class ApprovalLedger:
    """Single-host transactional SQLite approval ledger, NOT an agent runner."""
    def __init__(self, path, *, authorized_owner_id, identity_provider=None,
                 head_verifier=None):
        self.path = str(path)
        if not self.path or self.path == ":memory:" or self.path.startswith("file:"):
            raise ApprovalError("durable local database required")
        if type(authorized_owner_id) is not int or authorized_owner_id < 1:
            raise ApprovalError("invalid trusted owner ID")
        if head_verifier is None or not callable(getattr(head_verifier, "assert_current", None)):
            raise ApprovalError("trusted GitHub head verifier required")
        self._owner = authorized_owner_id
        self._identity = identity_provider if identity_provider is not None else GitHubUserIdentity()
        self._heads = head_verifier
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS approvals (
                job_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL,
                repository TEXT NOT NULL, pr_number INTEGER NOT NULL,
                head_sha TEXT NOT NULL, challenge_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','approved','revoked')),
                actor_id INTEGER, approved_at INTEGER,
                CHECK ((state='approved' AND actor_id IS NOT NULL AND approved_at IS NOT NULL)
                    OR (state!='approved' AND approved_at IS NULL)))""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL,
                received_at INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
                job_id TEXT NOT NULL, actor_id INTEGER,
                occurred_at INTEGER NOT NULL, detail_hash TEXT NOT NULL)""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS audit_immutable_update
                BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS audit_immutable_delete
                BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END""")
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _time(now):
        if now is None:
            return int(time.time())
        if type(now) is not int or now < 0:
            raise ApprovalError("invalid time")
        return now

    @staticmethod
    def _digest(value):
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise ApprovalError("invalid SHA-256")
        return value

    @staticmethod
    def _event(db, event, job, actor, at, detail):
        db.execute("INSERT INTO audit(event,job_id,actor_id,occurred_at,detail_hash) VALUES(?,?,?,?,?)",
                   (event, job, actor, at, hashlib.sha256(detail.encode()).hexdigest()))

    def propose(self, request, *, now=None, ttl=600):
        """Freeze owner-visible payload, return nonce once, persist its hash."""
        now = self._time(now)
        if type(ttl) is not int or not 60 <= ttl <= 3600:
            raise ApprovalError("unbounded expiration")
        data = validate_request(request)
        job = identity(data)
        nonce = secrets.token_urlsafe(32)
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO approvals(job_id,payload_digest,repository,
                pr_number,head_sha,challenge_hash,expires_at,state)
                VALUES(?,?,?,?,?,?,?,'pending')""",
                (job, job, data["repository"], data["pr_number"],
                 data["head_sha"], nonce_hash, now + ttl))
            self._event(db, "proposed", job, None, now, job)
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ApprovalError("duplicate job / no silent nonce reissue") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {"job_id": job, "payload_digest": job, "challenge": nonce,
                "expires_at": now + ttl, "state": "pending_owner_approval",
                "dispatch_enabled": False}

    def approve(self, *, job_id, payload_digest, challenge, user_access_token,
                now=None):
        """Authenticated one-use owner approval; no dispatch or launch."""
        now = self._time(now)
        self._digest(job_id)
        self._digest(payload_digest)
        if not isinstance(challenge, str) or not 32 <= len(challenge) <= 256:
            raise ApprovalError("invalid one-use challenge")
        owner = self._identity.owner_id(user_access_token)
        if type(owner) is not int or owner != self._owner:
            raise ApprovalError("owner identity mismatch")
        challenge_hash = hashlib.sha256(challenge.encode()).hexdigest()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT payload_digest,challenge_hash,head_sha,
                expires_at,state,pr_number FROM approvals WHERE job_id=?""",
                (job_id,)).fetchone()
            if (not row or row[4] != "pending" or now >= row[3] or
                    not hmac.compare_digest(row[0], payload_digest) or
                    not hmac.compare_digest(row[1], challenge_hash)):
                raise ApprovalError("expired, replayed or modified approval")
            self._heads.assert_current(row[5], row[2])
            cur = db.execute("""UPDATE approvals SET state='approved',
                actor_id=?,approved_at=?,challenge_hash=?
                WHERE job_id=? AND state='pending'""",
                (owner, now, hashlib.sha256(secrets.token_bytes(32)).hexdigest(), job_id))
            if cur.rowcount != 1:
                raise ApprovalError("approval race")
            self._event(db, "owner_approved_not_dispatched", job_id, owner, now, payload_digest)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {"job_id": job_id, "state": "approved_not_dispatched",
                "approved_by_user_id": owner, "dispatch_enabled": False}

    def revoke(self, job_id, *, user_access_token, now=None):
        now = self._time(now)
        self._digest(job_id)
        owner = self._identity.owner_id(user_access_token)
        if type(owner) is not int or owner != self._owner:
            raise ApprovalError("owner identity mismatch")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("""UPDATE approvals SET state='revoked',actor_id=?,
                approved_at=NULL WHERE job_id=? AND state!='revoked'""", (owner, job_id))
            if cur.rowcount != 1:
                raise ApprovalError("missing or revoked job")
            self._event(db, "owner_revoked", job_id, owner, now, job_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {"state": "revoked", "dispatch_enabled": False}

    def record_verified_webhook(self, *, body, signature, secret, delivery_id, now=None):
        """HMAC authentication precedes atomic durable delivery deduplication."""
        now = self._time(now)
        try:
            delivery = verify_webhook(body, signature, secret, delivery_id)
        except EvidenceError as exc:
            raise ApprovalError("webhook authentication rejected") from exc
        if not re.fullmatch(r"[0-9a-f-]{36}", delivery):
            raise ApprovalError("invalid delivery ID")
        digest = hashlib.sha256(body).hexdigest()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO deliveries VALUES(?,?,?)", (delivery, digest, now))
            self._event(db, "webhook_recorded", delivery, None, now, digest)
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ApprovalError("replayed webhook delivery") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {"delivery_id": delivery, "state": "recorded_untrusted_event",
                "owner_approved": False, "dispatch_enabled": False}

    def status(self, job_id, *, now=None):
        now = self._time(now)
        self._digest(job_id)
        db = self._connect()
        try:
            row = db.execute("""SELECT state,head_sha,expires_at,actor_id,approved_at
                FROM approvals WHERE job_id=?""", (job_id,)).fetchone()
            if not row:
                raise ApprovalError("job not found")
            state = "expired_hold" if now >= row[2] and row[0] != "revoked" else row[0]
            return {"state": state, "head_sha": row[1], "expires_at": row[2],
                    "approved_by_user_id": row[3], "approved_at": row[4],
                    "dispatch_enabled": False}
        finally:
            db.close()

    def audit_events(self):
        db = self._connect()
        try:
            return db.execute("""SELECT seq,event,job_id,actor_id,occurred_at,detail_hash
                FROM audit ORDER BY seq""").fetchall()
        finally:
            db.close()


def dispatch(*args, **kwargs):
    raise RuntimeError("DISPATCH DISABLED: independent certification and owner activation required")

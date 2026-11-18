"""Crash-conservative durable SQLite agent-slot reservations, dispatch disabled.

Only single-host SQLite storage with reliable filesystem locking is supported.
Lease timeout is NOT evidence a remote agent stopped: expired leases stay counted
until termination is independently confirmed. No provider/agent calls.
"""
from __future__ import annotations
import re
import sqlite3
import time
import uuid

MAX_AGENTS = 2
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class SlotError(ValueError):
    pass


class Slots:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ":memory:":
            raise SlotError("memory database not durable")
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS leases(
                job_id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL, role TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('reserved','running','quarantined','released')),
                heartbeat INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                generation INTEGER NOT NULL CHECK(generation>0))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL, generation INTEGER NOT NULL,
                event TEXT NOT NULL, at INTEGER NOT NULL)""")
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not IDENT.fullmatch(value):
            raise SlotError("invalid identifier")

    @staticmethod
    def _time(value):
        if value is None:
            return int(time.time())
        if type(value) is not int or value < 0:
            raise SlotError("invalid time")
        return value

    def acquire(self, job_id, provider, role, *, now=None, ttl=300):
        for name in (job_id, provider):
            self._id(name)
        if role not in ("coder", "reviewer") or type(ttl) is not int or not 10 <= ttl <= 3600:
            raise SlotError("invalid lease")
        now = self._time(now)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT state,generation FROM leases WHERE job_id=?",
                             (job_id,)).fetchone()
            if old and old[0] != "released":
                raise SlotError("duplicate active job")
            count = db.execute("""SELECT count(*) FROM leases
                            WHERE state IN ('reserved','running','quarantined')""").fetchone()[0]
            if count >= MAX_AGENTS:
                raise SlotError("agent slots exhausted")
            generation = old[1] + 1 if old else 1
            token = uuid.uuid4().hex
            db.execute("""INSERT INTO leases(job_id,token,provider,role,state,heartbeat,expires_at,generation)
                        VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(job_id) DO UPDATE SET
                        token=excluded.token, provider=excluded.provider,
                        role=excluded.role, state=excluded.state,
                        heartbeat=excluded.heartbeat, expires_at=excluded.expires_at,
                        generation=excluded.generation""",
                       (job_id, token, provider, role, "reserved", now, now + ttl, generation))
            db.execute("INSERT INTO events(job_id,generation,event,at) VALUES(?,?,?,?)",
                       (job_id, generation, "reserved", now))
            db.commit()
            return {"job_id": job_id, "token": token, "generation": generation,
                    "state": "reserved", "expires_at": now + ttl}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _transition(self, lease, action, *, now=None, ttl=None, termination_verified=False):
        now = self._time(now)
        if not isinstance(lease, dict) or not {"job_id","token","generation"} <= set(lease):
            raise SlotError("invalid lease token")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT token,generation,state,expires_at FROM leases WHERE job_id=?",
                             (lease["job_id"],)).fetchone()
            if not row or row[0] != lease["token"] or row[1] != lease["generation"]:
                raise SlotError("stale lease fence")
            state, expiry = row[2], row[3]
            if action == "heartbeat":
                if state not in ("reserved","running") or now > expiry:
                    raise SlotError("expired or inactive lease")
                if type(ttl) is not int or not 10 <= ttl <= 3600:
                    raise SlotError("invalid ttl")
                new_state, expiry = "running", now + ttl
            elif action == "quarantine":
                if state not in ("reserved","running") or now <= expiry:
                    raise SlotError("not expired")
                new_state = "quarantined"
            elif action == "release":
                if state == "released" or termination_verified is not True:
                    raise SlotError("termination not independently confirmed")
                new_state = "released"
            else:
                raise SlotError("unsupported action")
            db.execute("UPDATE leases SET state=?,heartbeat=?,expires_at=? WHERE job_id=?",
                       (new_state, now, expiry, lease["job_id"]))
            db.execute("INSERT INTO events(job_id,generation,event,at) VALUES(?,?,?,?)",
                       (lease["job_id"], lease["generation"], new_state, now))
            db.commit()
            return {"state": new_state, "expires_at": expiry}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def heartbeat(self, lease, *, now=None, ttl=300):
        return self._transition(lease, "heartbeat", now=now, ttl=ttl)

    def quarantine_expired(self, lease, *, now=None):
        return self._transition(lease, "quarantine", now=now)

    def release(self, lease, *, termination_verified=False, now=None):
        return self._transition(lease, "release", now=now,
                                termination_verified=termination_verified)

    def snapshot(self):
        db = self._connect()
        try:
            rows = db.execute("SELECT job_id,role,state,expires_at,generation FROM leases").fetchall()
            return [{"job_id": r[0], "role": r[1], "state": r[2],
                     "expires_at": r[3], "generation": r[4]} for r in rows]
        finally:
            db.close()

    def audit(self):
        db = self._connect()
        try:
            return db.execute("SELECT job_id,generation,event,at FROM events ORDER BY seq").fetchall()
        finally:
            db.close()


def dispatch(*args, **kwargs):
    raise RuntimeError("DISPATCH DISABLED")

#!/usr/bin/env python3
"""Unified Approval Queue: development-only, offline, nonexecuting.

Builds on PR #58's relay components (``approval_relay_inbox`` identities and
corrective tasks, ``approval_relay_policy`` exact-head CI rules). It provides:

* canonical, digest-bound queue records (one live decision per repository,
  action kind, PR, exact SHA and linked issue);
* a hash-chained, append-only event ledger with strict replay;
* verification (never creation) of owner approval attestations bound to the
  frozen record digest, with expiry, numeric actor ID and one-time nonces;
* read-only reconciliation against a trusted GitHub snapshot, which only
  *proposes* invalidation / completion / post-merge reconciliation records;
* deterministic prioritization with dependency and cycle handling;
* two-agent capacity accounting across providers and reviewer independence;
* independent exact-head review certification (never merge authorization).

Nothing here dispatches an agent, writes to GitHub, reads a clock, opens a
network connection, merges, deploys or changes IAM/finance controls. The
activation constants below are fixed ``False`` and no code path reads them as
a switch: there is no dispatch function to enable.
"""
import argparse
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path

try:  # repository-root imports (unit tests, ``python -m``)
    from scripts.approval_relay_inbox import KINDS, REPO, corrective_task, queue_item
    from scripts.approval_relay_policy import verify_exact_head_ci
except ImportError:  # pragma: no cover - direct ``python scripts/...`` CLI use
    from approval_relay_inbox import KINDS, REPO, corrective_task, queue_item
    from approval_relay_policy import verify_exact_head_ci

ACTIVATION_ENABLED = False
DISPATCH_ENABLED = False
MERGE_ENABLED = False
MAX_LIVE_AGENTS = 2
MAX_APPROVAL_TTL_SECONDS = 24 * 3600
MAX_TEXT = 4000

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RECORD_ID = re.compile(r"[0-9a-f]{24}\Z")
NONCE = re.compile(r"[0-9a-f]{32,64}\Z")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}\Z")
GENESIS = "0" * 64

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
# Security remediation first within a priority tier.
KIND_RANK = {"remediation": 0, "branch_correction": 1, "implementation": 2,
             "claude_review": 3, "security_merge": 4,
             "post_merge_reconciliation": 5}
# Role is derived from the kind, never supplied by the requester.
ROLE_FOR_KIND = {"remediation": "implementer", "branch_correction": "implementer",
                 "implementation": "implementer", "claude_review": "reviewer",
                 "security_merge": "owner",
                 "post_merge_reconciliation": "read_only_verifier"}
if not set(KIND_RANK) == set(KINDS) == set(ROLE_FOR_KIND):  # import-time tripwire
    raise RuntimeError("queue kinds diverged from PR #58 inbox kinds")

PENDING = "pending_owner_approval"
APPROVED = "approved_not_dispatched"
LIVE_STATES = frozenset({PENDING, APPROVED})
DONE_STATES = frozenset({"certified", "completed"})
STATES = LIVE_STATES | DONE_STATES | {"invalidated", "withdrawn"}
MUTABLE = ("state", "dispatch_enabled", "activation_enabled", "merge_enabled")
RECORD_KEYS = frozenset({
    "schema_version", "record_type", "id", "repository", "action_kind",
    "pr_number", "target_pr", "head_sha", "linked_issue", "priority",
    "depends_on", "title", "details", "agent_role", "payload_sha256", *MUTABLE})

EVENT_SOURCES = {
    "enqueue": {"owner-prepared-record", "trusted-reconciler"},
    "owner_decision": {"owner-attestation"},
    "invalidate": {"trusted-reconciler"},
    "complete": {"trusted-reconciler"},
    # A certification is authenticated by an owner certification attestation
    # over the digest-bound certification, never by this source label.
    "certify": {"owner-attestation"},
}
EVENT_KEYS = frozenset({"seq", "prev_hash", "event_type", "record_id", "source",
                        "at", "data", "event_hash"})
ATTESTATION_KEYS = frozenset({
    "version", "scheme", "key_id", "record_id", "payload_sha256", "repository",
    "action_kind", "pr_number", "head_sha", "decision", "approver_user_id",
    "issued_at", "expires_at", "nonce", "signature"})
ATTESTATION_SCHEME = "hmac-sha256-offline-reference"
# Domain-separated from approvals: distinct scheme, decision and key set, so an
# approval attestation can never be replayed as a certification or vice versa.
CERT_ATTESTATION_KEYS = ATTESTATION_KEYS | {"certification_sha256"}
CERT_ATTESTATION_SCHEME = "hmac-sha256-offline-reference-certification"
CERTIFICATION_KEYS = frozenset({
    "result", "record_id", "pr_number", "head_sha", "report_sha256",
    "reviewer_session_id", "ci_evidence_sha256", "session_ledger_sha256",
    "reviewed_range_sha256", "merge_enabled", "note"})
CERT_NOTE = ("Certification is not merge authorization; a security-sensitive "
             "merge needs a separate authenticated owner decision.")
RANGE_KEYS = frozenset({"source", "repository", "base_sha", "head_sha", "pr_numbers",
                        "contributor_session_ids"})
RANGE_SOURCE = "trusted-git-range-adapter"
UNAPPROVED_MERGE_HOLD = "SECURITY HOLD: PR merged at an unapproved exact head"
PROVIDERS = frozenset({"claude", "copilot", "codex", "other"})
SESSION_ROLES = frozenset({"implementer", "reviewer", "read_only_verifier"})
SESSION_KEYS = frozenset({"session_id", "provider", "role", "record_id",
                          "pr_number", "state", "lease_expires_at", "source"})
REPORT_KEYS = frozenset({
    "schema_version", "report_type", "repository", "pr_number", "head_sha",
    "base_ref", "scope", "reviewer_session_id", "reviewer_provider", "verdict",
    "findings", "provenance"})
FINDING_KEYS = frozenset({"id", "severity", "status"})
SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
BLOCKING = frozenset({"critical", "high", "medium"})
PULL_KEYS = frozenset({"number", "state", "draft", "merged", "head_sha",
                       "base_ref", "merge_commit_sha"})


def _int(value, minimum=1):
    return type(value) is int and value >= minimum


def _text(value):
    return isinstance(value, str) and 1 <= len(value.strip()) and len(value) <= MAX_TEXT


def _check_canonical(value):
    if value is None or type(value) in (bool, int, str):
        return
    if isinstance(value, float):
        raise ValueError("floats are not canonical")
    if isinstance(value, list):
        for item in value:
            _check_canonical(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("non-string key")
            _check_canonical(item)
        return
    raise ValueError("unsupported value type")


def canonical(value):
    """Deterministic UTF-8 JSON: sorted keys, compact, no floats or NaN."""
    _check_canonical(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _payload(record):
    return {k: v for k, v in record.items() if k not in MUTABLE and k != "payload_sha256"}


def _expected_id(kind, pr, target_pr, head, issue, title, details):
    if kind == "branch_correction":
        return corrective_task(pr, target_pr, head, details)["id"]
    return queue_item(kind, pr, head, title, details, issue)["id"]


def build_record(kind, pr_number, head_sha, priority, title, details,
                 linked_issue=None, depends_on=(), target_pr=None):
    """Create a canonical pending record. Identity reuses PR #58 inbox rules."""
    if kind not in KINDS:
        raise ValueError("unsupported action kind")
    if (kind == "branch_correction") != (target_pr is not None):
        raise ValueError("target_pr is required only for branch_correction")
    if (not isinstance(depends_on, (list, tuple)) or
            not all(isinstance(d, str) for d in depends_on)):
        raise ValueError("depends_on must be a list of record IDs")
    record_id = _expected_id(kind, pr_number, target_pr, head_sha, linked_issue,
                             title, details)
    record = {
        "schema_version": 1, "record_type": "unified_queue_entry",
        "id": record_id, "repository": REPO, "action_kind": kind,
        "pr_number": pr_number, "target_pr": target_pr, "head_sha": head_sha,
        "linked_issue": linked_issue, "priority": priority,
        "depends_on": sorted(set(depends_on)),
        "title": title, "details": details, "agent_role": ROLE_FOR_KIND[kind],
        "state": PENDING, "dispatch_enabled": False,
        "activation_enabled": False, "merge_enabled": False,
    }
    record["payload_sha256"] = sha256_hex(_payload(record))
    validate_record(record)
    return record


def validate_record(record, allowed_states=frozenset({PENDING})):
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ValueError("record fields missing/unexpected")
    if record["schema_version"] != 1 or type(record["schema_version"]) is not int:
        raise ValueError("unsupported record schema")
    if record["record_type"] != "unified_queue_entry" or record["repository"] != REPO:
        raise ValueError("record type/repository not allowed")
    kind = record["action_kind"]
    if (not isinstance(kind, str) or kind not in KINDS or
            record["agent_role"] != ROLE_FOR_KIND[kind]):
        raise ValueError("kind/role mismatch")
    if not _int(record["pr_number"]):
        raise ValueError("invalid PR number")
    if kind == "branch_correction":
        if not _int(record["target_pr"]) or record["target_pr"] == record["pr_number"]:
            raise ValueError("invalid target PR")
    elif record["target_pr"] is not None:
        raise ValueError("unexpected target PR")
    if not isinstance(record["head_sha"], str) or not SHA.fullmatch(record["head_sha"]):
        raise ValueError("invalid exact head")
    if record["linked_issue"] is not None and not _int(record["linked_issue"]):
        raise ValueError("invalid linked issue")
    if not isinstance(record["priority"], str) or record["priority"] not in PRIORITY_RANK:
        raise ValueError("invalid priority")
    deps = record["depends_on"]
    if (not isinstance(deps, list) or
            not all(isinstance(d, str) and RECORD_ID.fullmatch(d) for d in deps) or
            deps != sorted(set(deps)) or record["id"] in deps):
        raise ValueError("invalid dependencies")
    if not _text(record["title"]) or not _text(record["details"]):
        raise ValueError("invalid title/details")
    if not isinstance(record["state"], str) or record["state"] not in allowed_states:
        raise ValueError("record state not allowed here")
    if (record["dispatch_enabled"] is not False or record["activation_enabled"] is not False
            or record["merge_enabled"] is not False):
        raise ValueError("attempt to enable dispatch/activation/merge")
    if record["id"] != _expected_id(kind, record["pr_number"], record["target_pr"],
                                    record["head_sha"], record["linked_issue"],
                                    record["title"], record["details"]):
        raise ValueError("record ID does not match canonical identity")
    if record["payload_sha256"] != sha256_hex(_payload(record)):
        raise ValueError("frozen payload digest mismatch")
    return True


def _slot(record):
    return (record["action_kind"], record["pr_number"], record["target_pr"],
            record["linked_issue"])


# --------------------------------------------------------------------------
# Owner approval attestations: verification only. This module cannot sign.
# --------------------------------------------------------------------------

def _verify_owner_mac(att, record, *, keys, scheme, decisions, record_states,
                      record_error, extra_bindings, keyring, approver_ids, now,
                      used_nonces, max_ttl):
    """Shared verifier for owner approval and certification attestations."""
    if not isinstance(att, dict) or set(att) != keys:
        raise ValueError("attestation fields missing/unexpected")
    if att["version"] != 1 or type(att["version"]) is not int or att["scheme"] != scheme:
        raise ValueError("unsupported attestation scheme")
    if not isinstance(keyring, dict) or not isinstance(att["key_id"], str):
        raise ValueError("no trusted verification key")
    key = keyring.get(att["key_id"])
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("no trusted verification key")
    try:
        validate_record(record, allowed_states=record_states)
    except (ValueError, TypeError):
        raise ValueError(record_error) from None
    bindings = [(field, record[rec_field]) for field, rec_field in (
        ("record_id", "id"), ("payload_sha256", "payload_sha256"),
        ("repository", "repository"), ("action_kind", "action_kind"),
        ("pr_number", "pr_number"), ("head_sha", "head_sha"))]
    for field, value in bindings + list(extra_bindings):
        if type(att[field]) is not type(value) or att[field] != value:
            raise ValueError("attestation not bound to this exact record: " + field)
    if not isinstance(att["decision"], str) or att["decision"] not in decisions:
        raise ValueError("invalid decision")
    if (not _int(att["approver_user_id"]) or not isinstance(approver_ids, frozenset)
            or att["approver_user_id"] not in approver_ids):
        raise ValueError("approver is not an allowlisted owner ID")
    if not (_int(att["issued_at"], 0) and _int(att["expires_at"], 0) and _int(now, 0)):
        raise ValueError("invalid attestation time")
    if not att["issued_at"] <= now < att["expires_at"]:
        raise ValueError("attestation expired or not yet valid")
    if att["expires_at"] - att["issued_at"] > max_ttl:
        raise ValueError("attestation lifetime too long")
    if not isinstance(att["nonce"], str) or not NONCE.fullmatch(att["nonce"]):
        raise ValueError("invalid nonce")
    if att["nonce"] in used_nonces:
        raise ValueError("nonce replay")
    if not isinstance(att["signature"], str) or not DIGEST.fullmatch(att["signature"]):
        raise ValueError("invalid signature encoding")
    body = canonical({k: v for k, v in att.items() if k != "signature"})
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, att["signature"]):
        raise ValueError("signature verification failed")
    return att["decision"]


def verify_attestation(att, record, *, keyring, approver_ids, now, used_nonces,
                       max_ttl=MAX_APPROVAL_TTL_SECONDS):
    """Return ``approve``/``reject`` for a valid attestation, else raise.

    GitHub comment text, labels, checkboxes or model output are never inputs
    here; only a structured attestation whose MAC covers every bound field.
    """
    return _verify_owner_mac(
        att, record, keys=ATTESTATION_KEYS, scheme=ATTESTATION_SCHEME,
        decisions=("approve", "reject"), record_states=frozenset({PENDING}),
        record_error="record is not a valid pending owner decision", extra_bindings=(),
        keyring=keyring, approver_ids=approver_ids, now=now, used_nonces=used_nonces,
        max_ttl=max_ttl)


def validate_certification(cert, record):
    """Exact-shape check of a certification produced by ``certify``.

    Shape alone is never sufficient: replay additionally requires an owner
    certification attestation over ``sha256_hex(cert)``.
    """
    if not isinstance(cert, dict) or set(cert) != CERTIFICATION_KEYS:
        raise ValueError("invalid certification data")
    if (cert["result"] != "exact_head_certified" or cert["merge_enabled"] is not False or
            cert["note"] != CERT_NOTE or
            type(cert["record_id"]) is not str or cert["record_id"] != record["id"] or
            type(cert["pr_number"]) is not int or cert["pr_number"] != record["pr_number"] or
            type(cert["head_sha"]) is not str or cert["head_sha"] != record["head_sha"]):
        raise ValueError("invalid certification data")
    for field in ("report_sha256", "ci_evidence_sha256", "session_ledger_sha256",
                  "reviewed_range_sha256"):
        if not isinstance(cert[field], str) or not DIGEST.fullmatch(cert[field]):
            raise ValueError("invalid certification evidence digest")
    if (not isinstance(cert["reviewer_session_id"], str) or
            not SESSION_ID.fullmatch(cert["reviewer_session_id"])):
        raise ValueError("invalid certification reviewer")
    return True


def verify_certification_attestation(att, record, certification, *, keyring, approver_ids,
                                     now, used_nonces, max_ttl=MAX_APPROVAL_TTL_SECONDS):
    """Verify an owner attestation that binds an exact certification digest to
    an approved review record. Returns ``certify`` or raises."""
    validate_certification(certification, record)
    return _verify_owner_mac(
        att, record, keys=CERT_ATTESTATION_KEYS, scheme=CERT_ATTESTATION_SCHEME,
        decisions=("certify",), record_states=frozenset({APPROVED}),
        record_error="record is not an approved review awaiting certification",
        extra_bindings=(("certification_sha256", sha256_hex(certification)),),
        keyring=keyring, approver_ids=approver_ids, now=now, used_nonces=used_nonces,
        max_ttl=max_ttl)


# --------------------------------------------------------------------------
# Append-only, hash-chained event ledger.
# --------------------------------------------------------------------------

def _event_hash(event):
    return sha256_hex({k: v for k, v in event.items() if k != "event_hash"})


def replay(events, *, keyring=None, approver_ids=frozenset(), anchor_hash=None,
           trusted_now=None):
    """Strictly replay a ledger; any irregularity fails closed.

    ``anchor_hash`` is the externally retained head hash; supplying it detects
    truncation or wholesale replacement, which a chain alone cannot.
    ``trusted_now`` (epoch seconds from a trusted clock) rejects future-dated
    events. Attestation freshness is evaluated at each event's ``at``; only
    ``append`` (which stamps ``at`` from the trusted clock) binds that time to
    real time, so a replayed ledger is only as fresh as its write path.
    """
    if not isinstance(events, list):
        raise ValueError("ledger must be a list")
    if trusted_now is not None and not _int(trusted_now, 0):
        raise ValueError("invalid trusted time")
    records, nonces, slots = {}, set(), {}
    prev, last_at = GENESIS, 0
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise ValueError("event fields missing/unexpected at %d" % index)
        if type(event["seq"]) is not int or event["seq"] != index:
            raise ValueError("non-contiguous sequence at %d" % index)
        if event["prev_hash"] != prev:
            raise ValueError("broken hash chain at %d" % index)
        if not isinstance(event["event_hash"], str) or event["event_hash"] != _event_hash(event):
            raise ValueError("event hash mismatch at %d" % index)
        etype, source = event["event_type"], event["source"]
        if (not isinstance(etype, str) or not isinstance(source, str) or
                etype not in EVENT_SOURCES or source not in EVENT_SOURCES[etype]):
            raise ValueError("event type/source not allowed at %d" % index)
        if not _int(event["at"], 0) or event["at"] < last_at:
            raise ValueError("non-monotonic event time at %d" % index)
        if trusted_now is not None and event["at"] > trusted_now:
            raise ValueError("event dated after trusted time at %d" % index)
        if not isinstance(event["data"], dict):
            raise ValueError("invalid event data at %d" % index)
        rid = event["record_id"]
        if not isinstance(rid, str) or not RECORD_ID.fullmatch(rid):
            raise ValueError("invalid record ID at %d" % index)
        data = event["data"]
        if etype == "enqueue":
            if set(data) != {"record"}:
                raise ValueError("invalid enqueue data")
            record = data["record"]
            validate_record(record)
            if source == "trusted-reconciler" and record["action_kind"] != "post_merge_reconciliation":
                raise ValueError("reconciler may only enqueue post-merge reconciliation")
            if record["id"] != rid or rid in records:
                raise ValueError("duplicate or mismatched enqueue at %d" % index)
            live = slots.get(_slot(record))
            if live is not None and records[live]["state"] in LIVE_STATES:
                raise ValueError("live decision already exists for this slot; invalidate it first")
            records[rid] = dict(record)
            slots[_slot(record)] = rid
        else:
            if rid not in records:
                raise ValueError("event for unknown record at %d" % index)
            record = records[rid]
            state, kind = record["state"], record["action_kind"]
            if etype == "owner_decision":
                if set(data) != {"attestation"}:
                    raise ValueError("invalid owner decision data")
                decision = verify_attestation(data["attestation"], record, keyring=keyring,
                                              approver_ids=approver_ids, now=event["at"],
                                              used_nonces=nonces)
                nonces.add(data["attestation"]["nonce"])
                record["state"] = APPROVED if decision == "approve" else "withdrawn"
            elif etype == "invalidate":
                if (set(data) != {"reason", "observed_head_sha"} or not _text(data["reason"]) or
                        (data["observed_head_sha"] is not None and
                         (not isinstance(data["observed_head_sha"], str) or
                          not SHA.fullmatch(data["observed_head_sha"])))):
                    raise ValueError("invalid invalidation data")
                if state not in LIVE_STATES:
                    raise ValueError("cannot invalidate a terminal record")
                record["state"] = "invalidated"
            elif etype == "complete":
                if set(data) != {"evidence"} or not _text(data["evidence"]):
                    raise ValueError("invalid completion data")
                if state != APPROVED or kind == "claude_review":
                    raise ValueError("only approved non-review records can complete")
                record["state"] = "completed"
            elif etype == "certify":
                if set(data) != {"certification", "attestation"}:
                    raise ValueError("invalid certification data")
                validate_certification(data["certification"], record)
                if state != APPROVED or kind != "claude_review":
                    raise ValueError("only approved review records can be certified")
                verify_certification_attestation(
                    data["attestation"], record, data["certification"], keyring=keyring,
                    approver_ids=approver_ids, now=event["at"], used_nonces=nonces)
                nonces.add(data["attestation"]["nonce"])
                record["state"] = "certified"
        prev, last_at = event["event_hash"], event["at"]
    if anchor_hash is not None and anchor_hash != prev:
        raise ValueError("ledger head does not match retained anchor (truncated or replaced)")
    return {"records": records, "used_nonces": frozenset(nonces), "head_hash": prev}


def append(events, event_type, record_id, source, at, data, **replay_kwargs):
    """Return a new ledger with one validated event; the input is not mutated.

    Re-enqueueing an identical record is an idempotent no-op; a conflicting
    payload under the same ID fails closed. ``trusted_now`` is required and the
    event time must equal it: the writer cannot back-date an event to keep an
    expired attestation inside its validity window.
    """
    trusted_now = replay_kwargs.get("trusted_now")
    if not _int(trusted_now, 0):
        raise ValueError("append requires trusted_now from a trusted clock")
    if type(at) is not int or at != trusted_now:
        raise ValueError("event time must equal trusted time at append")
    state = replay(events, **replay_kwargs)
    if event_type == "enqueue" and record_id in state["records"]:
        existing = state["records"][record_id]
        if (isinstance(data, dict) and isinstance(data.get("record"), dict) and
                data["record"].get("payload_sha256") == existing["payload_sha256"]):
            return list(events)
        raise ValueError("conflicting payload for existing record ID")
    event = {"seq": len(events), "prev_hash": state["head_hash"], "event_type": event_type,
             "record_id": record_id, "source": source, "at": at, "data": data}
    event["event_hash"] = _event_hash(event)
    new = list(events) + [event]
    replay(new, **replay_kwargs)
    return new


def _reject_constant(value):
    raise ValueError("non-finite number rejected: " + value)


def _reject_float(value):
    raise ValueError("float rejected: " + value)


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key: " + key)
        out[key] = value
    return out


def strict_json(text):
    return json.loads(text, object_pairs_hook=_no_duplicates,
                      parse_constant=_reject_constant, parse_float=_reject_float)


def loads_ledger(text):
    if not isinstance(text, str):
        raise ValueError("ledger must be text")
    if text == "":
        return []
    if not text.endswith("\n"):
        raise ValueError("ledger must end with a newline (possible truncation)")
    lines = text[:-1].split("\n")
    if any(not line.strip() for line in lines):
        raise ValueError("blank ledger line")
    return [strict_json(line) for line in lines]


def dumps_ledger(events):
    return "".join(canonical(e).decode("utf-8") + "\n" for e in events)


# --------------------------------------------------------------------------
# Read-only GitHub reconciliation (proposals only).
# --------------------------------------------------------------------------

def normalize_pull(raw):
    """Map a GitHub REST pull object to the minimal snapshot shape.

    GitHub populates ``merge_commit_sha`` with a *test* merge commit for open
    PRs, so it is kept only when ``merged_at`` shows a real merge.
    """
    if not isinstance(raw, dict):
        raise ValueError("invalid pull object")
    head, base = raw.get("head"), raw.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise ValueError("invalid pull refs")
    merged = raw.get("merged_at") is not None
    pull = {"number": raw.get("number"), "state": raw.get("state"),
            "draft": raw.get("draft"), "merged": merged,
            "head_sha": head.get("sha"), "base_ref": base.get("ref"),
            "merge_commit_sha": raw.get("merge_commit_sha") if merged else None}
    _validate_pull(pull)
    return pull


def _validate_pull(pull):
    if not isinstance(pull, dict) or set(pull) != PULL_KEYS:
        raise ValueError("pull fields missing/unexpected")
    if not _int(pull["number"]) or pull["state"] not in ("open", "closed"):
        raise ValueError("invalid pull identity/state")
    if type(pull["draft"]) is not bool or type(pull["merged"]) is not bool:
        raise ValueError("invalid pull flags")
    if not isinstance(pull["head_sha"], str) or not SHA.fullmatch(pull["head_sha"]):
        raise ValueError("invalid pull head")
    if not isinstance(pull["base_ref"], str) or not pull["base_ref"]:
        raise ValueError("invalid pull base")
    if pull["merged"]:
        if (pull["state"] != "closed" or not isinstance(pull["merge_commit_sha"], str)
                or not SHA.fullmatch(pull["merge_commit_sha"])):
            raise ValueError("inconsistent merged pull")
    elif pull["merge_commit_sha"] is not None:
        raise ValueError("merge commit on unmerged pull")


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {
            "source", "repository", "fetched_at", "main_sha", "pulls"}:
        raise ValueError("snapshot fields missing/unexpected")
    if snapshot["source"] != "trusted-readonly-github-adapter" or snapshot["repository"] != REPO:
        raise ValueError("untrusted snapshot source/repository")
    if not _int(snapshot["fetched_at"], 0):
        raise ValueError("invalid snapshot time")
    if not isinstance(snapshot["main_sha"], str) or not SHA.fullmatch(snapshot["main_sha"]):
        raise ValueError("invalid main SHA")
    pulls = snapshot["pulls"]
    if not isinstance(pulls, dict):
        raise ValueError("invalid pulls")
    for key, pull in pulls.items():
        _validate_pull(pull)
        if key != str(pull["number"]):
            raise ValueError("pull key/number mismatch")
    return True


def post_merge_record(pr_number, merge_commit_sha, main_sha, priority="P1"):
    details = ("Read-only post-merge reconciliation for PR #%d. Verify with GET-only "
               "GitHub/Git reads that merge commit %s is reachable from protected main "
               "(observed main %s), required checks on main are green, and propose "
               "closing superseded queue entries. No writes, dispatch, deploy, IAM or "
               "finance changes." % (pr_number, merge_commit_sha, main_sha))
    return build_record("post_merge_reconciliation", pr_number, merge_commit_sha, priority,
                        "Post-merge read-only reconciliation for PR #%d" % pr_number, details)


def reconcile(records, snapshot):
    """Compare live records with a trusted read-only snapshot.

    Returns proposals only (``applied`` is always False). It never proposes an
    approval, certification, dispatch or merge.
    """
    validate_snapshot(snapshot)
    pulls = {int(k): v for k, v in snapshot["pulls"].items()}
    proposals, holds, merged_prs = [], [], {}
    for rid in sorted(records):
        record = records[rid]
        if record["state"] not in LIVE_STATES or record["action_kind"] == "post_merge_reconciliation":
            continue
        kind = record["action_kind"]
        pr = pulls.get(record["pr_number"])
        head_pr = pulls.get(record["target_pr"]) if kind == "branch_correction" else pr
        if pr is None or head_pr is None:
            holds.append({"record_id": rid, "reason": "PR absent from trusted snapshot; cannot verify"})
            continue
        if pr["merged"]:
            merged_prs.setdefault(pr["number"], []).append(record)
            if (kind == "security_merge" and record["state"] == APPROVED and
                    pr["head_sha"] == record["head_sha"]):
                proposals.append({"proposal": "complete", "record_id": rid,
                                  "data": {"evidence": "PR merged at approved head; merge commit "
                                           + pr["merge_commit_sha"]}})
            elif kind == "security_merge":
                # A merge without a verified owner decision at that exact head
                # is a control failure, not routine supersession.
                holds.append({"record_id": rid, "reason": UNAPPROVED_MERGE_HOLD,
                              "security_alert": True, "owner_action_required": True,
                              "decision_state": record["state"],
                              "decision_head_sha": record["head_sha"],
                              "observed_head_sha": pr["head_sha"],
                              "merge_commit_sha": pr["merge_commit_sha"]})
                proposals.append({"proposal": "invalidate", "record_id": rid,
                                  "data": {"reason": UNAPPROVED_MERGE_HOLD +
                                           "; owner security review required",
                                           "observed_head_sha": pr["head_sha"]}})
            else:
                proposals.append({"proposal": "invalidate", "record_id": rid,
                                  "data": {"reason": "PR merged; decision superseded",
                                           "observed_head_sha": pr["head_sha"]}})
            continue
        if kind == "branch_correction" and head_pr["merged"]:
            proposals.append({"proposal": "invalidate", "record_id": rid,
                              "data": {"reason": "target PR merged; correction superseded",
                                       "observed_head_sha": head_pr["head_sha"]}})
            continue
        if pr["state"] == "closed" or head_pr["state"] == "closed":
            proposals.append({"proposal": "invalidate", "record_id": rid,
                              "data": {"reason": "PR closed without merge",
                                       "observed_head_sha": head_pr["head_sha"]}})
            continue
        if head_pr["head_sha"] != record["head_sha"]:
            proposals.append({"proposal": "invalidate", "record_id": rid,
                              "data": {"reason": "stale SHA: PR head moved",
                                       "observed_head_sha": head_pr["head_sha"]}})
    for number in sorted(merged_prs):
        group = merged_prs[number]
        priority = min((r["priority"] for r in group), key=PRIORITY_RANK.__getitem__)
        record = post_merge_record(number, pulls[number]["merge_commit_sha"],
                                   snapshot["main_sha"], priority)
        if record["id"] not in records:
            proposals.append({"proposal": "enqueue", "record_id": record["id"],
                              "data": {"record": record}})
    for p in proposals:
        p["source"] = "trusted-reconciler"
    return {"proposals": proposals, "holds": holds, "applied": False,
            "dispatch_enabled": False}


# --------------------------------------------------------------------------
# Capacity accounting and deterministic prioritization (advisory only).
# --------------------------------------------------------------------------

def validate_sessions(sessions):
    if not isinstance(sessions, list):
        raise ValueError("sessions must be a list")
    seen = set()
    for s in sessions:
        if not isinstance(s, dict) or set(s) != SESSION_KEYS:
            raise ValueError("session fields missing/unexpected")
        if not isinstance(s["session_id"], str) or not SESSION_ID.fullmatch(s["session_id"]):
            raise ValueError("invalid session ID")
        if s["session_id"] in seen:
            raise ValueError("duplicate session ID")
        seen.add(s["session_id"])
        if s["provider"] not in PROVIDERS or s["role"] not in SESSION_ROLES:
            raise ValueError("unknown provider/role")
        if s["record_id"] is not None and (not isinstance(s["record_id"], str) or
                                           not RECORD_ID.fullmatch(s["record_id"])):
            raise ValueError("invalid session record ID")
        if not _int(s["pr_number"]) or s["state"] not in ("active", "ended"):
            raise ValueError("invalid session PR/state")
        if not _int(s["lease_expires_at"], 0):
            raise ValueError("invalid lease")
        if s["source"] != "owner-attested-session-ledger":
            raise ValueError("unattested session record")
    return True


def capacity(sessions, now):
    """Count live agents across providers. An expired lease that was never
    recorded as ended still counts: we cannot prove the agent stopped."""
    validate_sessions(sessions)
    if not _int(now, 0):
        raise ValueError("invalid time")
    live = [s for s in sessions if s["state"] == "active"]
    return {"live": len(live), "limit": MAX_LIVE_AGENTS,
            "available": max(0, MAX_LIVE_AGENTS - len(live)),
            "unverified_expired_leases": sorted(s["session_id"] for s in live
                                                if s["lease_expires_at"] <= now)}


def _implementer_sessions(sessions, pr_number):
    return {s["session_id"] for s in sessions
            if s["role"] == "implementer" and s["pr_number"] == pr_number}


def admission(request, records, sessions, now):
    """Advise whether the owner may manually start one session. Never starts
    one, never pre-empts or ends an active session."""
    cap = capacity(sessions, now)
    out = {"admissible": False, "capacity": cap, "dispatch_enabled": False,
           "note": "Advisory only; the owner starts any session manually."}
    if (not isinstance(request, dict) or
            set(request) != {"session_id", "provider", "role", "record_id"} or
            not all(isinstance(v, str) for v in request.values())):
        out["reason"] = "malformed request"
        return out
    record = records.get(request["record_id"])
    if record is None:
        out["reason"] = "unknown queue record"
    elif record["state"] != APPROVED:
        out["reason"] = "record lacks verified owner approval"
    elif record["agent_role"] == "owner":
        out["reason"] = "owner-only decision; no agent may execute merges"
    elif request["role"] != record["agent_role"] or request["provider"] not in PROVIDERS:
        out["reason"] = "role/provider not allowed for this record"
    elif (request["role"] == "reviewer" and
          request["session_id"] in _implementer_sessions(sessions, record["pr_number"])):
        out["reason"] = "reviewer must be independent of implementer"
    elif (not isinstance(request["session_id"], str) or
          not SESSION_ID.fullmatch(request["session_id"]) or
          any(s["session_id"] == request["session_id"] for s in sessions)):
        out["reason"] = "invalid or reused session ID"
    elif cap["available"] < 1:
        out["reason"] = "two-agent cap reached"
    elif record["priority"] != "P0" and cap["available"] == 1 and any(
            r["priority"] == "P0" and r["state"] in LIVE_STATES and
            r["agent_role"] != "owner" and
            not any(s["state"] == "active" and s["record_id"] == r["id"] for s in sessions)
            for r in records.values()):
        out["reason"] = "last slot reserved for unstaffed P0 work"
    else:
        out["admissible"] = True
        out["reason"] = "capacity available; owner may start manually"
    return out


def plan(records, sessions, now):
    """Deterministic ordering of live decisions with dependency status."""
    cap = capacity(sessions, now)
    live = {rid: r for rid, r in records.items() if r["state"] in LIVE_STATES}
    in_cycle, visiting, done = set(), [], set()

    def visit(rid):
        if rid in done or rid not in live:
            return
        if rid in visiting:
            in_cycle.update(visiting[visiting.index(rid):])
            return
        visiting.append(rid)
        for dep in live[rid]["depends_on"]:
            visit(dep)
        visiting.pop()
        done.add(rid)

    for rid in sorted(live):
        visit(rid)
    order = []
    for rid, r in sorted(live.items(), key=lambda kv: (
            PRIORITY_RANK[kv[1]["priority"]], KIND_RANK[kv[1]["action_kind"]],
            kv[1]["pr_number"], kv[0])):
        reason = None
        if rid in in_cycle:
            reason = "dependency cycle"
        else:
            for dep in r["depends_on"]:
                if dep not in records:
                    reason = "unknown dependency " + dep
                elif records[dep]["state"] in ("invalidated", "withdrawn"):
                    reason = "dependency %s is %s" % (dep, records[dep]["state"])
                elif (r["action_kind"] == "security_merge" and
                      records[dep]["action_kind"] == "claude_review"):
                    review = records[dep]
                    if review["state"] != "certified":
                        reason = "dependency %s is not a certified review" % dep
                    elif (review["repository"] != REPO or r["repository"] != REPO or
                          type(review["pr_number"]) is not int or
                          type(r["pr_number"]) is not int or
                          review["pr_number"] != r["pr_number"] or
                          review["head_sha"] != r["head_sha"]):
                        reason = "dependency certified for a different repository, PR or head"
                elif records[dep]["state"] not in DONE_STATES:
                    reason = "dependency %s not complete" % dep
                if reason:
                    break
        if reason:
            status = "blocked"
        elif r["state"] == PENDING:
            status = "awaiting_owner_decision"
        else:
            status = "approved_awaiting_manual_start"
        entry = {"id": rid, "priority": r["priority"], "action_kind": r["action_kind"],
                 "pr_number": r["pr_number"], "head_sha": r["head_sha"], "status": status}
        if reason:
            entry["reason"] = reason
        order.append(entry)
    return {"order": order, "capacity": cap, "dispatch_enabled": False,
            "activation_enabled": False, "merge_enabled": False}


# --------------------------------------------------------------------------
# Independent exact-head review certification.
# --------------------------------------------------------------------------

def _validate_range(reviewed_range, record):
    """Trusted description of the full reviewed diff ``base..head``: every PR
    in the stack and every agent session that authored a commit in it."""
    if not isinstance(reviewed_range, dict) or set(reviewed_range) != RANGE_KEYS:
        return "reviewed range fields missing/unexpected"
    if reviewed_range["source"] != RANGE_SOURCE or reviewed_range["repository"] != REPO:
        return "untrusted reviewed range"
    for field in ("base_sha", "head_sha"):
        if not isinstance(reviewed_range[field], str) or not SHA.fullmatch(reviewed_range[field]):
            return "invalid reviewed range"
    if reviewed_range["head_sha"] != record["head_sha"]:
        return "reviewed range does not end at the exact head"
    prs, contributors = reviewed_range["pr_numbers"], reviewed_range["contributor_session_ids"]
    if (not isinstance(prs, list) or not all(_int(n) for n in prs) or
            len(prs) != len(set(prs)) or record["pr_number"] not in prs):
        return "invalid reviewed range PRs"
    if (not isinstance(contributors, list) or
            not all(isinstance(c, str) and SESSION_ID.fullmatch(c) for c in contributors) or
            len(contributors) != len(set(contributors))):
        return "invalid reviewed range contributors"
    return None


def certify(record, report, *, current_head_sha, sessions, ci_policy_source,
            applicable_workflows, ci, reviewed_range, ci_target=None,
            lookup_workflow_evidence=None):
    """Evaluate an imported review report (untrusted data) for exact-head
    certification. A certification is never merge authorization, and on its
    own it cannot change ledger state: replay additionally requires an owner
    certification attestation over the returned, digest-bound result."""
    def no(reason):
        return {"result": "not_certified", "reason": reason, "merge_enabled": False}

    validate_sessions(sessions)
    if not isinstance(record, dict) or record.get("action_kind") != "claude_review":
        return no("not an independent review record")
    if record.get("state") != APPROVED:
        return no("review record lacks verified owner approval")
    try:
        validate_record(record, allowed_states=frozenset({APPROVED}))
    except (ValueError, TypeError):
        return no("invalid or synthetic review record")
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        return no("report fields missing/unexpected")
    if (report["schema_version"] != 1 or type(report["schema_version"]) is not int or
            report["report_type"] != "independent_security_review" or
            report["provenance"] != "trusted-report-import" or report["repository"] != REPO):
        return no("untrusted or unsupported report")
    if type(report["pr_number"]) is not int or report["pr_number"] != record["pr_number"]:
        return no("report targets a different PR")
    if (not isinstance(current_head_sha, str) or not SHA.fullmatch(current_head_sha) or
            report["head_sha"] != record["head_sha"] or current_head_sha != record["head_sha"]):
        return no("stale or mismatched exact head")
    if report["scope"] != "full-diff-exact-head" or report["base_ref"] != "main":
        return no("review scope is not full diff at exact head")
    if not isinstance(report["reviewer_session_id"], str):
        return no("reviewer session not admitted for this record")
    reviewer = [s for s in sessions if s["session_id"] == report["reviewer_session_id"]]
    if (len(reviewer) != 1 or reviewer[0]["role"] != "reviewer" or
            reviewer[0]["record_id"] != record["id"] or
            reviewer[0]["provider"] != report["reviewer_provider"]):
        return no("reviewer session not admitted for this record")
    range_reason = _validate_range(reviewed_range, record)
    if range_reason is not None:
        return no(range_reason)
    stack_implementers = set()
    for number in reviewed_range["pr_numbers"]:
        stack_implementers |= _implementer_sessions(sessions, number)
    if (report["reviewer_session_id"] in stack_implementers or
            report["reviewer_session_id"] in reviewed_range["contributor_session_ids"]):
        return no("self-certification: reviewer contributed to the reviewed range")
    if report["verdict"] != "PASS":
        return no("verdict is not PASS")
    findings = report["findings"]
    if not isinstance(findings, list):
        return no("malformed findings")
    for f in findings:
        if (not isinstance(f, dict) or set(f) != FINDING_KEYS or not _text(f["id"]) or
                not isinstance(f["severity"], str) or f["severity"] not in SEVERITIES or
                not isinstance(f["status"], str) or
                f["status"] not in ("open", "resolved", "accepted_residual")):
            return no("malformed finding")
        if f["severity"] in BLOCKING and f["status"] != "resolved":
            return no("unresolved blocking finding " + f["id"])
    # ci_target comes from a separate trusted GitHub read, not the report
    # or CI claims; the shared verifier authenticates each supplied run ID.
    if (not isinstance(ci_target, dict) or ci_target.get("repository") != REPO
            or type(ci_target.get("number")) is not int
            or ci_target["number"] != record["pr_number"]
            or ci_target.get("head_sha") != record["head_sha"]
            or ci_target.get("base_branch") != "main"
            or ci_target.get("state") != "open" or ci_target.get("draft") is not True):
        return no("trusted exact protected-main target unavailable or mismatched")
    ci_reason, verified_packets = verify_exact_head_ci(
        ci_policy_source, applicable_workflows, ci, ci_target,
        lookup_workflow_evidence=lookup_workflow_evidence)
    if ci_reason is not None:
        return no(ci_reason)
    try:
        digests = {
            "report_sha256": sha256_hex(report),
            "ci_evidence_sha256": sha256_hex({"ci_policy_source": ci_policy_source,
                                              "applicable_workflows": applicable_workflows,
                                              "target": ci_target,
                                              "verified_runs": list(verified_packets)}),
            "session_ledger_sha256": sha256_hex(sessions),
            "reviewed_range_sha256": sha256_hex(reviewed_range)}
    except (ValueError, TypeError, RecursionError):
        return no("certification evidence is not canonical")
    return dict(digests, result="exact_head_certified", record_id=record["id"],
                pr_number=record["pr_number"], head_sha=record["head_sha"],
                reviewer_session_id=report["reviewer_session_id"], merge_enabled=False,
                note=CERT_NOTE)


# --------------------------------------------------------------------------
# Read-only CLI.
# --------------------------------------------------------------------------

def _read_json(path):
    return strict_json(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline unified approval queue (read-only).")
    parser.add_argument("mode", choices=("verify", "plan", "reconcile"))
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--anchor", help="retained ledger head hash")
    parser.add_argument("--sessions", type=Path, help="session ledger JSON (plan)")
    parser.add_argument("--snapshot", type=Path, help="trusted snapshot JSON (reconcile)")
    parser.add_argument("--now", dest="evaluation_time", type=int, help="evaluation time, epoch seconds (plan)")
    args = parser.parse_args(argv)
    try:
        # The CLI has no keyring: any approval event fails closed here.
        state = replay(loads_ledger(args.ledger.read_text(encoding="utf-8")),
                       anchor_hash=args.anchor)
        if args.mode == "verify":
            result = {"head_hash": state["head_hash"],
                      "records": {k: v["state"] for k, v in sorted(state["records"].items())}}
        elif args.mode == "plan":
            if args.sessions is None or args.evaluation_time is None:
                raise ValueError("plan requires --sessions and --now")
            result = plan(state["records"], _read_json(args.sessions), args.evaluation_time)
        else:
            if args.snapshot is None:
                raise ValueError("reconcile requires --snapshot")
            result = reconcile(state["records"], _read_json(args.snapshot))
    except (ValueError, OSError, TypeError) as exc:
        print("FAIL CLOSED: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Offline policy prototype for the trusted execution bridge (Issue #80).

NOT AN AUTHENTICATOR OR DISPATCHER. Inputs are untrusted until a separate,
independently certified trusted adapter supplies verified evidence. This module
never calls a provider, GitHub, a subprocess, or an approval workflow.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

REPOSITORY = "ScottMDavistv/azure-text-to-video"
SHA = re.compile(r"[0-9a-f]{40}\Z")
KINDS = frozenset({"security_remediation", "security_review", "integration", "feature"})
PRIORITY = {"security_remediation": 0, "security_review": 1, "integration": 2, "feature": 3}
MAX_AGENTS = 2
MAX_SAFE_TEXT = 4096
IMMUTABLE = ("schema_version", "repository", "kind", "pr_number", "head_sha",
             "scope", "instructions", "requested_agent")


def _plain_object(value: Any) -> bool:
    return type(value) is dict and all(type(k) is str for k in value)


def _text(value: Any, limit: int = MAX_SAFE_TEXT) -> bool:
    if type(value) is not str or not value or len(value) > limit:
        return False
    # Include Unicode format/bidi controls, unpaired surrogates and line separators.
    return not any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                   for c in value)


def parse_json(raw: str) -> dict:
    """Reject ambiguous duplicate keys, NaN and other nonstandard constants."""
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj

    def bad_constant(value):
        raise ValueError("nonstandard JSON constant: " + value)

    data = json.loads(raw, object_pairs_hook=unique, parse_constant=bad_constant)
    if not _plain_object(data):
        raise ValueError("expected JSON object")
    return data


def validate_request(request: dict) -> dict:
    """Validate shape, not owner approval or independent trust."""
    if not _plain_object(request) or set(request) != set(IMMUTABLE):
        raise ValueError("invalid request fields")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("unsupported schema")
    if request["repository"] != REPOSITORY or request["kind"] not in KINDS:
        raise ValueError("invalid repository or kind")
    if type(request["pr_number"]) is not int or request["pr_number"] < 1:
        raise ValueError("invalid PR number")
    if type(request["head_sha"]) is not str or not SHA.fullmatch(request["head_sha"]):
        raise ValueError("invalid exact head")
    if not all(_text(request[f]) for f in ("scope", "instructions")):
        raise ValueError("untrusted or malformed text")
    if request["requested_agent"] not in ("claude", "copilot"):
        raise ValueError("unapproved agent kind")
    return dict(request)


def identity(request: dict) -> str:
    safe = validate_request(request)
    canonical = json.dumps(safe, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pending_job(request: dict) -> dict:
    """An entry in a pending manual queue, NEVER an approved authorization."""
    safe = validate_request(request)
    return {"job_id": identity(safe), "request": safe,
            "state": "pending_owner_approval", "dispatch_enabled": False}


def ordered_pending(items: list) -> list:
    """Deduplicate identical pending jobs; hold on collisions or active states."""
    if type(items) is not list:
        raise ValueError("invalid queue")
    seen = {}
    for item in items:
        if not _plain_object(item) or set(item) != {"job_id", "request", "state",
                                                   "dispatch_enabled"}:
            raise ValueError("invalid job")
        if item["state"] != "pending_owner_approval" or item["dispatch_enabled"] is not False:
            raise ValueError("active or approved job supplied as pending")
        job_id = identity(item["request"])
        if item["job_id"] != job_id:
            raise ValueError("identity mismatch")
        if job_id in seen and seen[job_id] != item:
            raise ValueError("conflicting duplicate")
        seen[job_id] = item
    return sorted(seen.values(), key=lambda x:
                  (PRIORITY[x["request"]["kind"]], x["request"]["pr_number"],
                   x["job_id"]))


def capacity_policy(snapshot: dict) -> dict:
    """Advisory HOLD/ELIGIBLE only. Caller-provided snapshot is NOT attestation.

    A future trusted adapter must authenticate leases, sessions, costs,
    approvals, and provider launch capability before consuming this result.
    """
    if not _plain_object(snapshot) or set(snapshot) != {
        "active_agents", "active_reviewers", "capacity_complete",
        "credits_verified", "paid_overages_disabled", "provider_supported",
        "approval_trusted", "head_unchanged", "kill_switch_disabled"
    }:
        return {"state": "hold", "reason": "missing capacity evidence"}
    agents = snapshot["active_agents"]
    reviewers = snapshot["active_reviewers"]
    if (type(agents) is not int or type(reviewers) is not int or
            agents < 0 or reviewers < 0 or agents + reviewers >= MAX_AGENTS):
        return {"state": "hold", "reason": "agent capacity unavailable"}
    checks = ("capacity_complete", "credits_verified", "paid_overages_disabled",
              "provider_supported", "approval_trusted", "head_unchanged",
              "kill_switch_disabled")
    if any(snapshot[key] is not True for key in checks):
        return {"state": "hold", "reason": "unverified trust or budget gate"}
    return {"state": "eligible_for_manual_handoff",
            "reason": "advisory only; authenticated dispatcher not implemented"}


def dispatch(*_args, **_kwargs):
    """Deliberate invariant: there is no executable launch path."""
    raise RuntimeError("DISPATCH DISABLED: no certified adapter or owner activation")

# Approval-gated GitHub relay — development-only pilot

**Status:** specification and offline validator only. NOT ACTIVE. No webhook, scheduled task, GitHub Actions workflow, Claude runner, external API credential, or automated dispatcher is installed by this change.

## Purpose and trust boundaries
Connect ChatGPT's owner-supervised GitHub interactions to Claude Code cloud sessions using GitHub PR/Issue records as transport. A GitHub connection does **not** itself establish direct inter-agent messaging or programmatic Claude session launch.

- Outgoing assignments and remediation dispatches require a distinct, explicit owner approval for the exact immutable message, repository, target PR, and head SHA.
- Inbound findings can be retrieved without owner approval, but are **untrusted** until verified for origin, exact head, scope and evidence. Retrieval is not equivalent to auto-executing instructions.
- Do not assume comment text, labels, issue authorship, or a human-readable 'approved' string is owner authorization. Future active dispatcher must verify authentic GitHub actor ID, approval provenance, scope, one-time nonce, TTL and a SHA-256 payload digest using trusted GitHub API data. A GitHub comment by itself is not a sufficient approval token.
- Keep existing protected-main checks, required security re-review, workflow-approval requirements, no Azure deployment or IAM changes, FAL disabled, and financial controls untouched.
- At most two agents may execute concurrently. Independent security reviewer must not author or repair the code it certifies. Security remediation takes priority over ordinary coding.
- No cost-incurring API or GitHub-hosted workflow execution authorized. No secrets or tokens copied into comments, logs or artifacts.

## Proposed record contract (version 1)
Canonical JSON (UTF-8) is the payload; compute SHA-256 over deterministic serialization (sorted keys, compact separators, Unicode preserved). Required keys:

```json
{
  "schema_version": 1,
  "handoff_id": "vrl-pr46-security-review-001",
  "kind": "security_review",
  "repository": "ScottMDavistv/azure-text-to-video",
  "pr_number": 46,
  "base_ref": "main",
  "head_sha": "<40 lowercase hex characters from GitHub at dispatch>",
  "target_agent": "claude",
  "scope": "entire PR diff against protected main",
  "instructions": "independent full-diff review; source and test evidence; no code edits",
  "approval": {
    "state": "pending",
    "approved_by_user_id": null,
    "approved_payload_sha256": null,
    "approved_at": null,
    "expires_at": null,
    "nonce": null
  },
  "execution": {"state": "disabled", "provider": "manual-claude-cloud"}
}
```

Offline validator in `scripts/approval_relay_validate.py` accepts **pending-only** records. It never authenticates, sends work, executes commands, or authorizes a dispatch. Live approval and author verification must be implemented and independently reviewed before any enablement.

## Proposed state machine (not implemented)
1. `draft` → `pending_owner_approval`: prepare request, pin current PR head and canonical payload digest, display proposed exact handoff to owner.
2. Owner approves *this* digest and *this* target only through an authenticated channel (never by a model interpreting a comment). Capture immutable GitHub user ID, nonce, expiry and approval evidence in an append-only audit record.
3. `approved` → `dispatched`: **separate future activation**; re-fetch PR head and branch protections, verify digest, nonce unused, approval unexpired, budget policy and worker identity, then invoke only an explicitly allowlisted execution mechanism. Fail closed on mismatch.
4. `dispatched` → `reported`: import review report as data. Require reviewer identity/provenance, exact head SHA, full-diff scope, test evidence, and blocking-verdict fields.
5. `reported` → `remediation_pending_owner_approval`: coding agent's proposed assignments are new outgoing requests; never dispatch automatically.
6. `re_review_required`: any PR head change invalidates previous exact-head CI/review. Require a new independently authored full-diff verdict.
7. `completed`: exact-head CI passes, negative-path security tests run, independent reviewer explicitly passes, no unresolved blockers; **does not imply authorization to merge**.

## PR #46 pilot — exact-head, independent full-diff security review
At preparation time (2026-11-17), GitHub reported PR #46 open/draft, 17 changed files, head `73fe6d2a8aeeef97d34ba23c83fcce5e63f649cc`, base `main`. These are snapshots, **not dispatch-time evidence**.

Review complete diff against protected main, including: NF-02–NF-08, admin/OIDC mappings and distinct admin authorizations, bearer UTF-8/Unicode regression fixtures, provider request uniqueness and receipt canonicalization, finance concurrency and Azurite negative paths, Copilot identity/auto-merge trust boundary, Actions/OIDC and publication provenance, MSAL sign-in/consent/logout, and disabled paid execution. Require an independent verdict covering all findings at *the final exact head*. Verify applicable exact-head CI before the verdict; a green run from a previous commit is insufficient.

Manual zero-overage proof-of-concept: owner reviews a frozen proposed message; after approval, owner may initiate an existing subscription-backed Claude cloud session **manually**, without GitHub Actions or Anthropic API keys. Claude publishes a report to an approved GitHub issue/PR location. ChatGPT may retrieve the report via its GitHub connection. This workflow has not been launched by this PR.

## Future automatic execution prerequisites — separate owner decision
- Establish and document a supported non-metered Claude launch mechanism; **do not infer one from GitHub App installation**.
- Verify Anthropic plan usage and paid extra-usage/auto-reload settings, and GitHub Actions included budget/overage controls. If any path charges separately, **block** and escalate to owner.
- Independently certify least-privilege dispatcher authentication and approval attestation, replay protection, timeout, idempotency, explicit allowlists, prompt-injection resistance, non-billable fail-closed policy, redaction and protected-main compliance.
- Test expired approval, tampered digest, modified PR head, duplicate nonce, forged GitHub commenter/actor ID, malicious report instructions, reviewer=author conflict, missing verdict, budget exhaustion and parallel-agent cap.
- Keep all workflow approvals manual until privileged secrets are independently verified isolated. No automatic dispatch, merge, deploy, IAM, or financial-control changes under this document.

## Acceptance checklist for this draft
- [ ] Offline validator unit tests pass locally and in existing CI if configured; no claim of CI pass without a matching run.
- [ ] Independent security review of new relay diff completed.
- [ ] Owner approves any outgoing PR #46 security-review handoff after refreshing exact SHA.
- [ ] Claude pilot report retrieved and provenance independently checked.
- [ ] Exact-head CI and security re-review of PR #46 completed; PR #46 remains draft until owner authorizes a security-sensitive merge.


## Standard engineering relay policies (owner decision 2026-11-17)

The offline decision engine is `scripts/approval_relay_policy.py`, with tests at `tests/design_management/test_approval_relay_policy.py`. **These rules are committed as a nonexecuting implementation; no trigger, agent runner, GitHub Actions workflow or unattended reader has been activated.**

### A. Wrong-base PR detection and narrowly safe automatic correction

For every new Copilot coding task, record the target PR number, exact target head SHA, target head branch, and independently verified coding branch *creation origin*. The desired coding PR base is the **target PR's head branch**, not `main`.

A future independently reviewed adapter may **automatically retarget only the PR base field** (not merge/rebase, force-push, create a replacement, or alter code), and only when all of the following are established from trusted GitHub metadata:
- Repository, task, head, draft state, expected current base and target branch match the allowlist.
- Branch-origin attestation proves coding branch started at exactly the owner-approved target PR head; self-reported PR text is not proof.
- The target PR head remains exactly the approved SHA, both PRs are open, and the retarget is guarded by an immediate re-fetch / compare-and-swap style check against concurrent changes.
- The adapter has minimally scoped permissions and emits an audit record with old/new base, immutable task ID, origin evidence, current head and action result.

If **any** condition fails, fail closed, flag wrong-origin and propose creating a **new coding branch from the target exact head**. The agent must not use a normal retarget as a substitute for ancestry correction. Do not rewrite branch history automatically. In particular, PR #63's already-corrected base does **not** prove its branch started from PR #46's head.

The current offline planner only returns `aligned`, `propose_retarget` or `block`; no PR mutation endpoint is called. Do not present a proposal as a completed correction. Because GitHub's generic PR update endpoint cannot atomically couple a head check to an update, active execution additionally needs a reviewed concurrency/race strategy; on ambiguity, stop.

### B. Mandatory exact-head origin for *every* coding branch

Create coding branches from the target PR's current, full, immutable SHA, not from default `main`. Re-check immediately before branch creation and require persisted trusted provenance. If the target head moved before creation or assignment, discard/recreate the request; if after creation, block further automated integration and request a refreshed plan. This requirement applies even if the coding PR visually shows zero files changed.

### C. Automatically prepare (never send) Claude re-review

After a coding agent finishes and its authorized changes are **verified incorporated into the target PR head** by trusted Git ancestry (not an issue comment or just an open child PR), fetch fresh CI for that exact head. Verify all required workflows and checks are completed/success, scope and provenance are correct, no stale review SHA, and the target remains open/draft. Then produce one immutable pending-owner-approval message targeting independent Claude for **full-diff** re-review. No automatic launch, GitHub mention, network dispatch, or approval assumption. If any check is missing/pending, state `blocked` with a reason; a subsequent evaluation can retry. Deduplicate by target PR + SHA + review-kind; a new SHA invalidates the old draft.

For the PR #46 pilot, the outgoing Claude request remains subject to explicit owner approval and manual subscription-backed Claude Code cloud start. CI is necessary but not sufficient: the reviewer must verify source and tests, accepted residual-risk severity, and any open security comments. A PASS is not authorization to merge.

### Testing and activation boundaries

Offline tests simulate correct and wrong bases, wrong origin, stale target, forged provenance, failed or stale CI, unintegrated changes, and non-draft/closed targets. They **do not** prove that a live GitHub adapter handles race conditions or authenticates branch-creation events. Before enabling automatic detection or base-only correction, add read-only GitHub adapter tests, protected-main/ruleset tests, least-privilege verification, and independent security review; no paid Actions or Claude automation may be introduced without separate approval and billing verification.

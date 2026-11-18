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
Canonical JSON (UTF-8) is the payload; compute SHA-256 over deterministic serialization (sorted keys, compact separators, Unicode preserved, no NaN). **Exclude the entire mutable top-level `approval` and `execution` fields before serialization and hashing**; do not exclude any other keys. The hash binds the immutable request, not mutable state. Required keys:

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


## Standard CI gate, corrective task and unified approval queue — owner authorization 2026-11-17

### Strict ancestry CI gate — staged, not active

`scripts/approval_relay_ci_gate.py` performs fail-closed verification of coding PR identity, target exact-head SHA, true merge-base ancestry, the expected base, draft status, owner-approved assignment and trusted creation-origin attestation. Missing or unverified provenance is a failure; PR titles, comments, labels and agent-supplied JSON do not constitute authentic proof.

A proposed *non-executable* GitHub Actions workflow is stored at `docs/relay-workflow-proposals/copilot-remediation-ancestry.yml`, **not** under `.github/workflows`. To turn it into a genuinely required check, future separately approved implementation must retrieve a trusted owner-approved task ledger and GitHub ancestry metadata using read-only API permissions, pin external action SHAs, guard against untrusted pull-request code and races, independently security-review the adapter, confirm no metered GitHub Actions usage, activate only within existing manual workflow-approval controls, and add the passing job's exact check name to the protected branch ruleset without removing any existing required check. Until those conditions are met, **the new mandatory check is not enforced by GitHub**. Never claim the offline unit tests alone protect PR merges.

### Failed alignment → corrective coding task record, NOT dispatch

`scripts/approval_relay_inbox.py` builds a deterministic, deduplicatable branch-correction issue body from a failure, linked to the coding PR, target PR, and exact head. A future authenticated publisher must search for the immutable dedupe marker and atomically handle concurrency to avoid repeated issues. Publication is an issue write, **not** a new coding-agent assignment; the owner must approve the outgoing assignment separately. Do not allow issue content to trigger Copilot dispatch under an existing controller or automation: pending tasks must remain excluded from dispatch allowlists.

The PR #63 test case was manually recorded at GitHub Issue #64, not by an active publisher; it is a blocked pending task and cannot activate any coding session.

### Unified decision-only approval queue

One inbox groups `branch_correction`, `remediation`, `claude_review`, and `security_merge` decisions. Each item is deduplicated by repository, kind, PR, exact head and linked issue, is `pending_owner_approval`, and has `dispatch_enabled=false` until authenticated owner attestation. Security remediation takes priority over ordinary work; at most two live agents. A newly changed SHA invalidates old review/merge approvals. An approving action must be one-time, scoped to the full frozen request digest, signed/authenticated with actor ID, expiry and nonce; no textual GitHub comment can grant it. Merge decisions remain distinct from dispatch approvals and require protected-main checks and independent reviewer gates.

The offline queue implementation does not persist or continuously poll anything. Before activation, provision a trusted store, concurrency-safe idempotency, immutable audit trail and read-only source ingestion, then independently review authentication and billing. No automatic Claude dispatch, paid Actions, or Azure deployment is approved.


## PR #58 post-review remediation — offline only (2026-11-17)

The offline review-preparation policy now requires binding integration evidence to the exact assigned coding PR ID, coding head SHA and target PR ID/head, as verified by trusted Git ancestry. A target not based on protected `main` is blocked. The corrective-task dedupe identity contains *both* coding and target PR numbers, preventing collisions across assignments. The strict ancestry verifier has direct function and subprocess CLI fail-closed tests.

The applicability of CI checks must be supplied by a protected, trusted task ledger based on actual workflow path filters and repository protections. The planner rejects absent, empty, duplicate, unknown, or untrusted applicability declarations; it checks all declared applicable workflows at the target exact head. Individual runs must carry independently attested GitHub workflow-file provenance (trusted workflow, path, protected-main revision and exact head). **A triggering actor is not workflow provenance.** Approved `workflow_dispatch` evidence may count only with separate trusted manual-run attestation; this does not authorize a dispatch. Do not use a caller-supplied snapshot or PR description as trusted evidence.

These checks remain an offline proposal, **not an activated GitHub required check or active collector**. The future adapter must independently authenticate GitHub metadata, policy applicability, approval provenance, workflow revision, race-safe current heads and branch creation. Independent security review and exact-head CI are still required before any development-only merge. Existing manual workflow approvals, protected-main rules, no paid FAL/overage, and no automatic agents remain unchanged.


## Unified Approval Queue — development-only implementation (Issue #65, 2026-11-18)

**Status: offline library and read-only CLI only. NOT ACTIVE.** `scripts/approval_relay_queue.py` (tests: `tests/design_management/test_approval_relay_queue.py`) adds no workflow, webhook, scheduler, GitHub write, agent launcher, network call or clock read. `ACTIVATION_ENABLED`, `DISPATCH_ENABLED` and `MERGE_ENABLED` are fixed `False`, and there is no dispatch or merge function to switch on. It reuses PR #58's inbox identities (`queue_item`, `corrective_task`, now with `implementation` and `post_merge_reconciliation` kinds) and the policy's exact-head CI rules (extracted unchanged into `verify_exact_head_ci`, now also failing closed on unhashable inputs).

| Component | Behavior |
|---|---|
| Canonical record | One live decision per `(action_kind, PR, target PR, linked issue)` **across all heads** (the slot deliberately omits the SHA, so a moved head must invalidate the old decision before a new one is enqueued); the record ID itself includes the exact SHA; ID recomputed from PR #58 inbox identity; SHA-256 over the frozen payload (canonical JSON, no floats/NaN); agent role derived from kind; dispatch/activation/merge flags must be `False`. |
| Durable ledger | Append-only JSONL, hash-chained from a zero genesis, contiguous sequence, monotonic time, strict parsing (duplicate keys, NaN, floats, blank lines, missing final newline rejected). Each event type has an allowlisted source; GitHub comments, agents and models are not sources, and source labels are never authentication. Replay re-verifies every owner approval **and every certification attestation**. `append` requires `trusted_now` and stamps the event time from it; replay with `trusted_now` rejects future-dated events. Malformed or unhashable fields fail closed with `ValueError`. Truncation is detectable only against an externally retained head hash (`anchor_hash`). |
| Owner approval | `verify_attestation` checks a structured attestation bound to record ID, payload digest, repository, kind, PR and head SHA; numeric owner ID allowlist; `issued_at ≤ now < expires_at`, TTL ≤ 24h; single-use nonce across the ledger; constant-time MAC check. Approval yields `approved_not_dispatched`; a merge decision never sets `merge_enabled`. The module cannot sign. |
| Read-only reconciliation | `reconcile` consumes a trusted snapshot and returns proposals only (`applied: false`): invalidate stale SHA / closed PRs, complete an approved merge decision only at the approved head, raise an owner-visible `security_alert` hold (`SECURITY HOLD: PR merged at an unapproved exact head`) when a `security_merge` decision's PR merged without an approval at that exact head, hold when a PR is absent, report a merged branch-correction target as superseded, and prepare a read-only `post_merge_reconciliation` record after a merge. `normalize_pull` drops GitHub's test merge commit on open PRs. The reconciler may enqueue only post-merge reconciliation records. |
| Prioritization | P0→P3, then security remediation → branch correction → implementation → review → merge → post-merge; deterministic tiebreak by PR and ID. Unknown, invalidated/withdrawn, incomplete and cyclic dependencies block. |
| Capacity | At most two `active` sessions across Claude/Copilot/Codex/other; an expired lease never recorded as ended still counts. Admission is advisory, requires prior verified approval and a kind-matching role, rejects session-ID reuse and owner-only merge records, reserves the last slot for unstaffed agent-executable P0 work (owner-only merge decisions never reserve it), and never pre-empts or ends a session. |
| Certification | `certify` validates the record itself (identity, digest, approved state), treats the review report as untrusted data with an exact key set (injected instruction fields rejected), and requires full-diff scope at the current exact head, a reviewer session admitted for that record, a trusted `reviewed_range` (`trusted-git-range-adapter`) ending at the exact head that lists every PR in the stack and every agent session that authored a commit in `base..head`, a reviewer that is neither a contributor nor an implementer of any stacked PR, PASS verdict, no unresolved critical/high/medium findings, and PR #58 exact-head CI rules. The result pins `report_sha256`, `ci_evidence_sha256`, `session_ledger_sha256` and `reviewed_range_sha256`. A ledger `certify` event is accepted only with an owner certification attestation (separate scheme and `certify` decision, single-use nonce shared with approvals) over `sha256(certification)`. Result is never merge authorization. |

### Remaining activation gates (each a separate owner decision)
1. Replace the offline HMAC reference scheme with asymmetric owner signatures (signing key held only on an owner-controlled device; agents and adapters hold verification keys only) and independently review it.
2. Provision a trusted durable ledger store with restricted write identity, externally retained head anchor, concurrency-safe append and backup.
3. Build and independently review a least-privilege, GET-only GitHub snapshot adapter (pulls, Actions runs with workflow-file provenance, rulesets) with race handling; `trusted-*` source tags are assertions by that adapter, not authentication.
4. Provision an owner-attested session ledger for capacity accounting; there is currently no live source of active-session truth. Reviewer independence is identifier-based: session IDs cannot detect one underlying agent appearing under two IDs (forked or sub-agent contexts, or sessions sharing a Project, memory or handoff docs). Before activation, the owner's certification attestation must also attest reviewer independence over the whole stacked range, covering no shared context and no fork lineage, and the range adapter must derive contributors from commit authors and session trailers for every commit in `base..head`.
4a. Provide a trusted clock to the ledger writer. Attestation freshness is evaluated at each event's `at`; only `append(..., trusted_now=...)` binds that to real time. A replayed ledger is only as fresh as its write path (gate 2), so the trusted store must accept writes solely through `append` with that clock.
5. Verify non-metered execution (GitHub Actions included minutes/spending limit, Anthropic plan usage) before any automation; none is enabled here.
6. Independent full-diff security review and exact-head CI of this change; protected-main rulesets, manual workflow approvals and the merge gate remain unchanged.

### PR #79 security remediation (2026-11-18, development-only)
Addresses the independent review at `c85f36e` (PR #79 comment 6979158122; authorization Issue #65 comment 6979738939): **M-1** ledger certifications now require an owner certification attestation over digest-pinned report, CI, session-ledger and reviewed-range evidence; a self-declared `trusted-report-import` event is rejected. **L-1** `certify` validates the record. **L-2** independence spans the whole stacked range (gate 4). **L-3** discriminating regressions plus an in-suite mutation harness (`tests/design_management/test_approval_relay_queue_mutations.py`) that fails if any listed control loses its test. **L-4** trusted-time `append` (gate 4a). **L-5** unapproved-head merges raise security holds. Informational: I-1 fixed (fail closed with `ValueError`), I-2 slot documented above, I-4 and I-5 fixed. I-3 (branch-correction identity ignores `linked_issue`; an invalidated ID cannot be re-enqueued) is fail-safe and kept, so PR #58 inbox identities stay unchanged. I-6 (96-bit record IDs) is acceptable offline and should be revisited before adversarial-scale use. Still **NOT ACTIVE**: this is no merge, dispatch or activation authorization.

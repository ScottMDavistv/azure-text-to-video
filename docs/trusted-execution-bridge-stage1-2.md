# Trusted execution bridge — Stage 1 evidence and Stage 2 capacity

**Development-only. No execution activation or security certification.** Issue #80, PR #81.

## Stage 1: read-only authenticated GitHub evidence

`scripts/trusted_github_evidence.py` offers:
- `GitHubReadOnly`: fixed-repository GET-only REST transport with out-of-band installation token, bounded API responses, strict same-origin pagination, fail-closed API/JSON failures.
- `verify_webhook`: SHA-256 HMAC validation of GitHub webhook bytes, with delivery ID shape validation. **Does not persist a replay nonce or establish owner approval.**
- `verify_pr_snapshot`: exact draft PR head and protected main SHA comparison against authenticated API responses.
- `verify_workflow_provenance`: fixed workflow file path/name AND matching protected-main and candidate blob SHA, plus associated successful PR run. A PR run's `refs/pull/N/merge` is not incorrectly required to match `refs/heads/main`.

**Not provided:** GitHub App creation, tokens or live webhook receiver; verified owner approval event and authorization ledger; authenticated reviewer identity and independence; out-of-band trust source for workflow blob allowlists. Workflow rules MUST come from a trusted protected configuration, never candidate PR text. The API adapter was tested with offline authenticated-response fixtures, not live secrets. Workflow run association/pagination requires further real-GitHub compatibility tests. Never treat a positive adapter result alone as authority to dispatch.

## Stage 2: durable agent-slot registry

`scripts/trusted_agent_slots.py` uses transactionally locked `BEGIN IMMEDIATE` SQLite writes, durable lease events and fencing generations. At most two reservations of either role `coder`/`reviewer` are allowed. A second process using the **same durable database** sees the reservation. Acquisitions, duplicates and reconnects fail closed.

An expired heartbeat quarantines the lease and **continues to occupy capacity**: releasing a remote agent's reservation on timeout alone might launch a third agent. Releasing requires `termination_verified=True`, which is a **caller assertion, not yet independently authenticated**; a future certified controller must authenticate the termination evidence and caller. Fencing prevents stale tokens from renewing old sessions but does not stop a remote agent by itself. `dispatch()` always raises.

**Deployment limitation:** SQLite is suitable only for a single reliable host/filesystem with shared locking and a controlled supervisor; copying the file between ChatGPT, Codespaces or cloud hosts does not provide shared agent capacity. Before cross-host use, choose a properly transactional shared DB and add verified worker liveness, service identity, and leader/lease recovery. No automatic expiry slot freeing.

## Security tests

`python -m unittest discover -s tests/design_management -p 'test_trusted_bridge_stage1_2.py' -v`

Covers HMAC failure, wrong exact SHA, path traversal, workflow-name spoofing, candidate workflow modification, incomplete pagination, missing token, out-of-repository REST requests, two-slot contention with three threads, durability across connections, stale fencing, timeout quarantine and unauthorized release. No credentials or external provider usage.

**Remaining certification gates:** fix #55 and #58 security findings; independent full-diff review of this implementation and authentic GitHub API fixtures; authoritative trusted approval ledger with authenticated actor ID/nonce/replay protection; durable verified shared capacity supervisor; and documented, safe, supported $0 incremental cost launch path. Merge and dispatch activation always require separate owner approval. Never infer capacity from comments or a planner output.

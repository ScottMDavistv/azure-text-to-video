# Trusted managed-queue execution bridge — offline prototype

**Issue:** #80. **Status:** development-only. **DO NOT ACTIVATE**.

This PR implements an **offline queue contract and policy prototype**, not a live dispatcher. The entrypoint `dispatch()` always raises. It neither contains GitHub network adapters nor Claude/Copilot provider calls, workflow definitions, secrets, GitHub writes, merges or paid usage.

## Trust model

`pending_job()` validates a strictly typed immutable request and creates a canonical SHA-256 identity, but **does not authenticate owner approval**. GitHub comments, labels and text are never approval tokens. `ordered_pending()` rejects any record masquerading as approved, executing or dispatch-enabled.

`capacity_policy()` is strictly **advisory**. Its input can be forged; therefore even its positive result is only `eligible_for_manual_handoff` and can never launch an agent. It counts reviewers alongside coders, blocks at two, holds on missing evidence, unknown costs, expired trust/head checks or an engaged kill switch. It is NOT a substitute for an atomic, durable concurrency controller.

## Future prerequisite work, NOT authorized for activation

1. Correct/independently certify PR #55 false-clean review findings and PR #58 workflow provenance and coding-PR identity.
2. Build an independently reviewed, authenticated trusted evidence adapter with strict workflow file paths and protected-main comparison, exact-head checks, complete GitHub API pagination and identity verification.
3. Provide a persistent transactional capacity ledger with atomic two-agent leases, fencing tokens, heartbeats, timeout recovery and cross-chat/global visibility; no inferred slot availability.
4. Identify a real provider-supported launch path and verify zero paid overages. If subscriptions cannot guarantee zero incremental charges, hold and require explicit owner decision; never use a paid API workaround.
5. Bind an immutable job digest to authenticated owner approval, nonce, TTL and scope; reject replay, request changes, forged comments, missing required CI and wrong branch ancestry.
6. Independent adversarial security certification including duplicate/reordered events, workflow spoofing, stale SHAs, concurrent agents, prompt injection, failed evidence, failed budgets and interruption recovery.
7. Separately obtain owner approval for **activation** and later for **each merge**.

## Tests

`python -m unittest discover -s tests/design_management -p 'test_trusted_execution_bridge.py' -v`

All proposed tests are local and free of provider calls. Passing them **does not** certify a real execution bridge.

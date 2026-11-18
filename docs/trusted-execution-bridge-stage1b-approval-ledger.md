# Stage 1B — trusted owner-approval and replay ledger (offline)

**Issue #80 / draft PR #81. Development only. DISPATCH DISABLED.**

The module `scripts/trusted_approval_ledger.py` stores a frozen, immutable-request SHA-256 digest, a hashed one-use nonce, short expiry, owner approval status, and an append-only SQLite audit trail. A separately authenticated GitHub user-access token is used to GET `https://api.github.com/user`, and the returned **numeric user ID** is compared with a trusted owner ID configured outside the candidate PR. Usernames, issue comments, labels, claimed actor objects, and installation-token identities are not approval authority.

**Authenticated head verification:** a trusted service constructs `GitHubHeadVerifier(GitHubReadOnly(installation_token))` and supplies it to the ledger. The ledger independently fetches the current exact PR SHA and draft/main state during the transaction. No caller-provided current-head assertion is accepted. The integration service must protect the construction/configuration of both the verifier and identity provider against candidate-PR inputs.

**Transaction/replay rules:** one immutable job digest per approval record; a random one-use challenge is shown only at preparation, persisted only as a SHA-256 digest and replaced upon approval. Approval expires after 60–3600 seconds and remains invalid after expiry or revocation. Duplicate jobs cannot silently generate a new challenge. GitHub webhook HMAC is checked before recording the delivery ID, which has a permanent unique DB constraint. Failed authentication does not consume a delivery ID. Attempts to reuse an approval or delivery are rejected. All writes use SQLite `BEGIN IMMEDIATE`.

**Audit limits:** audit events are append-only via SQLite triggers and retained for replay diagnostics, but an operating-system/database administrator can still tamper with the DB or remove triggers. Production-grade tamper evidence requires restricted file access, external backups and an independently protected append-only/hash-chained audit sink. There is no deployed receiver, OAuth callback, GitHub App registration, production secret or live owner approval in this PR.

**Single-host limits:** the SQLite file is safe only when shared by coordinated processes on one reliable filesystem. It is not a cross-host authorization database. The separate agent-slot ledger likewise needs a shared transactional backend before multi-host automatic use.

**Authorization boundary:** the result is exclusively `approved_not_dispatched`. No approval status is used as an execution permission by this module. `dispatch()` unconditionally fails. The authenticated controller for owner sessions, CSRF and OAuth state, RBAC, cancellation, verified recipient and purpose, TTL/nonce presentation, trusted head fetching and separate activation review is not built. An OAuth user token must be supplied out of band to a protected service, not stored in GitHub or committed into tests.

**Remaining checks:** authentic GitHub API compatibility and identity testing with a separately authorized app; independent full-diff security certification of PR #81, exact-head CI, remediation of PR #55/#58 security blockers, verified zero-overage provider launch, transactional global multi-host agent capacity, and an explicit owner decision for any activation and merge.

Offline regression: `python -m unittest discover -s tests/design_management -p 'test_trusted_approval_ledger.py' -v`.

**No GitHub App installation, webhook activation, agent dispatch, workflow activation, protected-main changes or financial privileges are authorized by this module.**

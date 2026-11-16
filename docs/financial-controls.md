# Bulk 02 — Financial Controls

Status: Development foundation. Not deployed. Paid FAL disabled.

## Included

- Integer-cent financial accounting
- Project-level and global FAL budget ceilings
- Atomic multi-record reservation operations
- ETag-based concurrent modification protection
- Reservation settlement
- Budget increase requests
- Two distinct administrator approval checks
- Persistent financial audit events
- Offline tests

## Critical integration requirements

These modules are NOT wired to rendering endpoints.

Before production:
1. Integrate trusted user identity and membership.
2. Establish secure administrative provisioning.
3. Create an authorized project budget.
4. Provision the global FAL spending ceiling.
5. Reserve before provider submission.
6. Verify provider identity and request cost.
7. Reconcile actual provider charges.
8. Preserve uncertain requests without releasing funds.
9. Add authorization and recovery integration tests.
10. Validate Azure Table transactional behavior.

## Limitations

- One partition provides atomicity but may create
  a throughput bottleneck at high job volume.
- ETag and transaction behavior requires Azure tests.
- Reservation settlement requires verified billing data.
- No automated provider billing reconciliation exists.
- No temporary spending-limit expiration exists yet.
- No notification delivery is implemented yet.
- No financial administration UI exists yet.
- No integration with the FAL worker exists yet.
- The administrator lookup must come from a trusted
  identity service, not client-supplied values.
- No audit log immutability guarantees are established.
- No live budget enforcement is active.

## Safety

No Azure resources are created.
No live FAL requests are made.
No infrastructure or application deployment occurs.
The main branch is unchanged.


## PR #3 security remediation — 2026-11-15

The signed-token financial boundary is implemented in
`backend/finance_authorization.py`, backed by
`backend/project_oidc.py`. Only properly signed Entra delegated
`access_as_user` tokens from the configured single tenant and audience
may be used. The user must have an **active internal user mapping**
before any finance write. Project spending requests look up current
membership from the backend project table, reject archived projects and
ignore all frontend-supplied roles. No financial endpoints are activated.

Administrator operations use a deployment-controlled immutable set of
SHA-256 digests of verified Entra issuer+subject identities, not UI
roles or caller-defined claims. The allowlist must be supplied by
trusted server configuration; never read it from HTTP request parameters.
Proposals prohibit self-approval, first approver replay and duplicate
approval by the same signed administrator. The existing primitive
finance methods remain backend-only and must never be directly exposed
over a network boundary; call the signed-token wrappers exclusively.

`finance_controls.settle` now fails closed unless a server-owned
`verify_provider_receipt(job_id, amount_cents, receipt)` callback
affirmatively returns `True`. The callback is a security boundary
and **must independently validate authenticated provider billing
evidence**, binding charge, request ID, job ID and receipt uniqueness.
A mock `lambda: True`, user-supplied receipt or bare UI confirmation
is never valid for live operations. **A real authenticated FAL receipt
verifier is not implemented or connected in Bulk 02**. No live paid
worker may be activated until it is.

CI now exercises signed RSA/JWT identity tests plus isolated
Azure Table SDK/Azurite cases for concurrent global/project limit
reservation, duplicate job IDs, double settlement, mismatched billing
evidence, lost transaction responses and fail-closed reconciliation.
Tests use emulator credentials only. Live-equivalent distributed Azure
tests and provider verification remain release gates.

IMPORTANT for stacked PR #2: the updated `request_increase`
signature no longer accepts caller-supplied `role`; `settle`
requires verified receipt and a verifier. Bulk 03 service adapters,
reconciliation and tests MUST be reconciled against these strengthened
interfaces before PR #2 is merged. Do not silently revert the
security checks to make downstream tests pass.

Azure job queues, Container Apps, FAL billing, and deployment remain
off for this development-only change.

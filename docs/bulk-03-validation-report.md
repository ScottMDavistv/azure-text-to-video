# Bulk 03 integration execution report

Branch: `feature/bulk-03-integrated`
Status: Implementation code and offline/Azurite testing, **not production activated**.

## Area 1 — Worker integration

Implemented `budgeted_worker_service.py` as an explicitly opt-in
queue-to-FAL adapter with two independent enable gates: call-site
`enabled=True` and `ENABLE_PAID_FAL_WORKER=true`. Before any provider
request, it verifies a canonical job/project mapping, trusted
authorization, durable project+global financial reservation and an
ETag-conditional job claim. It calls `budgeted_fal_execution.py`,
which uses the durable submission intent and guarded polling. Output
artifact metadata is persisted for later billing reconciliation.

**Not deployed or activated.** Existing `worker_cli.py` remains
check-only and does not call this adapter. A production operator must
review all worker/queue state transitions, manual recovery, and IAM
before enabling anything billable.

## Area 2 — Automatic reconciliation and notifications

Implemented durable, idempotency-keyed `financial_outbox.py`, held
notification and budget-approval hooks `budget_hold_events.py`,
one-shot scans in `safe_reconciliation.py`, and bounded
`reconciliation_runner.py`. The latter can enqueue in-app
notifications to an injected Azure Queue client with event_id-based
consumer deduplication. An approved budget request is verified from
the financial ledger before reconsideration.

**Not scheduled or delivered.** A production Azure Container App Job
schedule, trusted source of project IDs, identity/roles, real queue
and consumer, and operational monitoring must be provisioned.
Ambiguous delivery events remain held for explicit reconciliation.

## Area 3 — Concurrency and billing verification

Added Python concurrency simulations for ETag-based atomic budget
reservations, duplicate job IDs and dual admin approvals.
Added `test_azurite_finance_integration.py` and GitHub Actions
Azurite startup for actual Azure Table SDK atomic transactions
and simultaneous budget reservations, without production Azure.
Added `fal_billing_reconciliation.py` for provider receipt/request ID
binding, verified-charge settlement and manual review on overages
or uncertain submissions.

**Not verified with live Azure or FAL billing.** No authenticated FAL
billing data source has been configured, and no Azure account-level
identity/permissions or network conditions have been tested.

## Area 4 — Security integration

Implemented `project_oidc.py` with signed RS256 JWT verification,
issuer/audience/time claim checks, HTTPS JWKS host restriction and
pre-provisioned external-subject -> canonical internal UUID mapping.
Added `project_api_routes.py` for project-scoped held-job listing
and cancellation. Both require server-side project membership and
are disabled by default unless `ENABLE_PROJECT_ROUTES=true`.
The module imports and copies are included in the API Dockerfile.

**Not configured.** No trusted OIDC issuer, audience, JWKS URL or
identity mapping was supplied; routes remain disabled. The earlier
legacy /jobs API still uses service-token authorization and is not
yet a project-scoped public-user interface. A verified identity
provider and project onboarding flow are release prerequisites.

## GitHub Actions verification

The required `Backend tests (Python 3.12)` job now includes:
- Project, financial and FAL safety unit tests
- Held job scheduling, claim, coordinator and recovery tests
- Authenticated project API, outbox, notification, billing tests
- Deterministic budget and approval race simulations
- Real Azure Table SDK against local Azurite in CI
- Full backend regression suite

A green CI result certifies this source-code checkpoint only; it
does NOT authorize live FAL generation, Azure deployment or merging.

## Explicit blocking items

1. Live Azure deployment and IAM validation, including Container App
   scale, job trigger, queue visibility, and table transactions.
2. Trusted OIDC configuration and user/project mapping onboarding.
3. Scheduled reconciliation job and notification delivery consumer.
4. Authenticated provider billing integration and manual reconciliation
   of over-estimate/uncertain charges.
5. Production rollout review with the V5 design decision matrix.
6. Multi-project fairness persistence and distributed scheduler
   state, which remain preliminary candidate-selection logic.

No Azure deployment, live FAL request, pull request merge, or change
to `main` is part of this integration work.

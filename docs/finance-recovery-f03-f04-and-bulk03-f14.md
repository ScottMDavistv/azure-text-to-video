# F-03 / F-04 development remediation and F-14 compatibility assessment

Status: **experimental branch, not deployed, not approved for paid activation**.

## F-03: durable duplicate receipt prevention

`finance_recovery.settle_unique_receipt` verifies a provider request,
provider-scoped receipt ID, job ID, amount and supporting receipt object
with an injected independent verifier. The provider's authenticated
receipt verifier **does not exist here**. The same partition transaction
creates a unique `receipt:<sha256(provider:receipt_id)>` entity and
updates both spending ceilings and the job reservation. Concurrent
reuse of the same receipt causes the entire second transaction to fail.

Critical remaining requirement: bind the provider request ID to the
**durable server-side provider submission ledger** and verify the receipt
with real independently authenticated FAL billing evidence. The passed
callback itself cannot be trusted if a caller can supply it; never expose
this primitive as an HTTP endpoint. The old `finance_controls.settle` entry point now exists solely as a
**fail-closed compatibility guard** and raises `FinanceDenied`. The
experimental `settle_unique_receipt` is the only functional settlement
operation in this branch. It is still **not** an operational settlement
solution until trusted server-owned external billing proof exists.

## F-04: read-only reconciliation and dual-admin release prototype

`inspect_reservation` provides a read-only diagnosis of reservation
state. `request_unsubmitted_release` records the first verified
administrator's request with an atomic one-release-intent-per-job guard; `approve_unsubmitted_release` requires a
second distinct verified administrator, a recheck that the first
administrator has not been revoked, a separate conclusive
no-submission verification, and a single atomic project/global balance
release plus immutable-style audit event. ETag conflicts fail closed.

No verified `verify_never_submitted` source is implemented. A missing
local provider response, failed network connection, timeout or old job
status MUST NOT be treated as proof of no FAL request or charge.
Overruns and uncertain provider submissions still require a separate
review-and-reconciliation design. Real administrator authority must
come from operator-controlled Entra verification, not injected UI values.

## F-14: read-only assessment of PR #2

Verified PR #2 is `feature/bulk-03-integrated`. Its
`backend/budgeted_fal_execution.py::settle_verified_charge`
performs `settle(finance_table, job_id, charge_cents)` with no
`provider_receipt` or verifier; PR #3's stricter signature rejects it.
`backend/fal_billing_reconciliation.py` uses
`verify_provider_receipt(receipt, provider_request_id, amount)`
but PR #3 expects `verify(job_id, actual, receipt)`. Existing Bulk 03
logic forwards a simple ID/amount-matching lambda, not independently
verified FAL evidence, so it cannot prove billing authenticity. Further,
`paid_job_finalization.py` must use the durable receipt record in
addition to settled reservation state when approving job completion.

Recommended migration before integrating PR #2:

1. Choose one provider verifier contract explicitly binding
   `job_id, provider_request_id, receipt_id, actual_cents, receipt`.
2. Fetch the request ID from the trusted submission ledger and
   authenticate provider charge evidence independently, not via UI data.
3. Replace the legacy settlement call with the unique receipt
   settlement function after **all** entrypoints are audited. Check
   duplicate-receipt behavior across jobs and interrupted jobs.
4. Add end-to-end, unmocked Azurite integration for the actual Bulk 03
   path, including post-provider crash recovery and conflicting receipts.
5. Reconcile the changed branch against current Bulk 02; do not
   downgrade verified-receipt requirements to make old tests pass.

No edits to PR #2 were performed.

## Testing / safety

Local isolated Azurite tests cover duplicate provider receipt across
jobs, forged evidence refusal, independent two-admin release, double
release refusal, and simultaneous reuse of the same receipt. Tests use
mock trusted verification callbacks **only in tests**. They do not
validate real provider-origin authentication.

Paid provider defaults remain disabled. No deployment, no Azure IAM
changes, no paid FAL calls, and no PR #3 merge are part of this work.

# Bulk 03 Stage A — non-billable deployment preparation

This folder is a **configuration skeleton**, not deployed infrastructure.
No Azure deployment or paid FAL activation is authorized by CI.

## Required operator-provisioned dependencies

- Azure Container Apps environment + managed identities with access to
  specific Table, Queue and Blob Storage services (scope minimum required).
- A trusted Entra ID/OIDC issuer, audience, JWKS endpoint, and active
  issuer+subject -> canonical internal user mapping table.
- Separate finance, jobs, notification outbox, notification inbox and
  scheduler control tables; project mapping must be server verified.
- Scheduled reconciliation trigger (Container Apps Job with cron) and
  a deployed consumer that claims notification event IDs via
  `notification_inbox.apply_notification_once` before any side effect.
- Provider billing evidence source and operator reconciliation queue.
  FAL submission remains disabled. Do not provide FAL credentials to
  non-billable reconciliation jobs.

## Fail-closed gates

All deployments must set:
- `ENABLE_PAID_FAL_WORKER=false`
- `ENABLE_PROJECT_ROUTES=false` until OIDC is configured
- No `FAL_KEY` / `FAL_API_KEY` in non-billable validation services
- `VIDEO_PROVIDER=disabled`

A scheduled runner must invoke only vetted `run_stage_a_once` with
a server-selected list of canonical project IDs; not an HTTP parameter.
Notification retry/recovery must be enabled **only after** the
downstream inbox has been tested for event-id deduplication.
A production consumer must implement and test both claims and effects,
including interrupted side effects. Events stuck in processing need
manual reconciliation before repeated effects.

## Pre-deployment validation

`python -m stage_a_runtime_cli --check` exits nonzero on unsafe flags.
GitHub CI runs this check, tests the isolated Azure Table SDK via
Azurite, and smoke-tests the actual Docker image without Azure/FAL
credentials.

## Explicit hold points

1. The scheduler lease and fair candidate selection are wired into
   `stage_a_dispatch.dispatch_fair_once`, but no scheduled Azure job
   invokes this yet. Capacity reservation across tables remains a
   potential manual reconciliation case.
2. The verified billing/finalization bridge exists but has no
   authenticated FAL billing source and no live worker trigger.
3. Legacy `/jobs` endpoints remain service-token based; do not
   enable paid rendering through them.
4. Independent production security review, live Azure IAM tests, and
   provider billing validation are still required.

Never infer readiness from green Python or Docker tests alone.

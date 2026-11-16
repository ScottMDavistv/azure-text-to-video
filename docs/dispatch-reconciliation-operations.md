# Dispatch reconciliation operations — development runbook

**Status:** implemented in code and tested with local emulator. The recurring
Azure job and alerts are **not deployed or activated** by this PR.

## One-shot safety check (offline)

`PYTHONPATH=backend python -m dispatch_reconcile_cli --check`

This must succeed without network access or Azure credentials and verify
that paid FAL remains disabled.

## Controlled scheduling

`infra/dispatch-reconcile-job.bicep` defines a 15-minute Container Apps
scheduled job, defaulting to **preflight-only**. Deploy only after a separate
approved Azure change and RBAC review. To make it actually reconcile requires
`activateReconciliation=true`, an immutable tested image, a dedicated
user-assigned identity with *only* Table data and Queue send privileges,
managed-container log collection, a safe queue configuration, and explicit
owner approval. No provider credentials are required. The CLI uses
`--run-once` and a bounded scan, not an unbounded background daemon.

One run examines 25 queued jobs per page and up to 20 pages. A non-empty
continuation cursor after the cap is an alert, not a success signal. Repeated
runs start at the first page; for larger backlogs raise the cap within the
bounded maximum or add a separate durable sweep checkpoint before activation.

## Queue and Table integration

GitHub CI runs `test_azurite_dispatch_integration.py` against emulator ports
10001 (Queue) and 10002 (Table). It validates real Azure SDK calls and
conditional ETags under concurrent dispatch, a lost queue-send acknowledgement
and duplicate queue messages. It does **not** establish live Azure permissions
or exercise cloud SDK retry behavior. Perform supervised dev-Azure tests with
short-lived nonpaid jobs before production activation.

Queue delivery is at-least-once. Duplicate queue *messages* are possible, but
the legacy non-paid worker ETag claim allows only one queued-to-processing
claim. This safety does not authorize duplicate paid FAL submissions.

## Telemetry and alerts

`dispatch_reconcile_cli` logs aggregate JSON with no prompts, job IDs,
provider keys or receipt data:

- `inspected`, `pages`, `sent`, `held`
- `uncertain`, `already_dispatched`, `state_advanced`
- `incomplete` and `alert`

A sweep with uncertain queue responses or incomplete pagination emits the
fixed marker `dispatch_reconciliation_requires_attention`, sets
`alert=true`, and exits with code 2 when invoked through the CLI.

`infra/dispatch-recovery-alerts.bicep` defines a disabled-by-default
log-alert rule pointing at the existing workspace and an operator-supplied
Action Group. Before enabling, confirm console logs reach
`ContainerAppConsoleLogs_CL.Log_s`, validate the query with real harmless
test output, verify email/action-group delivery and measure costs. This rule
detects anomaly logs, not missing scheduled executions or upstream outage;
add a separate absence-of-heartbeat alert after telemetry is proven.

## No automatic deployment

Neither the scheduler nor the alert template is deployed by repository merge.
Never enable paid FAL or change finance limits to make reconciliation work.

# PR #28 to #45: dispatch reconciliation consolidation

**Canonical draft:** PR #45. **Original:** PR #28 is closed and unmerged.

## Preservation matrix

| Original #28 | #45 candidate | Finding |
|---|---|---|
| Bicep compilation and static negative-path tests | `dispatch-bicep-validation.yml` / backend readiness tests | Preserved, must rerun exact-head CI |
| Dedicated existing identity, ACR and environment references | `infra/dispatch-reconcile-job.bicep` | Preserved intent, **actual Azure identity/rights unverified** |
| Single-replica job with retries disabled, 300-second timeout | Scheduled job template | Preserved bounds |
| Default no paid provider and no secrets | Job template environment | Preserved, paid FAL stays disabled |
| Opt-in scheduled job | `resource scheduledReconciler = if (activateReconciliation)` | **Strengthened**: disabled default creates no scheduled resource (original could still incur recurring preflight costs) |
| Cadence | `cronExpression='0 * * * *'` | **Strengthened**: hourly future activation instead of every 15 minutes, still requires separate approval |
| Alert blueprints and custom marker | `infra/dispatch-recovery-alerts.bicep` | Preserved, disabled default; actual Log Analytics schema and Action Group delivery not verified |
| Static safeguards, cost/readiness record | Python validator / evidence documentation | Preserved, static only. No claim of Azure readiness |

## Cost and security hold

This is an infrastructure blueprint, not an actual scheduled deployment. Even with a 300-second maximum and hourly schedule, Azure may bill on execution, log ingestion and other meters. No budget, actual rate, ACR pull, dedicated reconciler identity assignment, Log Analytics table shape, Action Group receiver or safe live rollback was independently proven.

## Disposition

All six #28 paths survive in #45. Keep #45 draft as canonical and #28 closed/unmerged as historical comparison. No merge or Azure activation, permission assignment or provider billing authorized.

# P0 dispatch reconciliation — Azure deployment-readiness evidence

Status: **BLOCKED for deployment**. This is an evidence collection and design-review
document, not authorization to merge PR #28, grant RBAC, create cloud resources, or
activate any service. PR #24 must remain unmerged; paid FAL disabled.

## Scope and evidence boundaries

- Inspected: `infra/dispatch-reconcile-job.bicep`,
  `infra/dispatch-recovery-alerts.bicep`, `infra/worker-job.bicep`,
  `infra/identity.bicep`, `infra/main.bicep`.
- Offline CI: required backend regression/security checks, Bicep compilation,
  and `scripts/validate-dispatch-reconciliation-readiness.py` negative tests.
- This repository does **not** establish the actual state of the Azure tenant.
  Do not infer active Azure objects or assignments from Bicep `existing` declarations.

## Identity and permissions — not verified in live Azure

The reconciliation template requires `reconciliationIdentityName`, refers to an
*existing* user-assigned managed identity, and uses it for both ACR pull and
storage access. The existing `infra/identity.bicep` provisions an API identity,
not this reconciliation identity. No proof of a dedicated identity or its
assignments has been captured. A read-only inventory must confirm:

1. Dedicated reconciliation identity exists and is distinct from
   `id-textvideo-api-dev`, any paid worker identity, and deployment identities.
2. ACR pull permission is limited to the approved registry.
3. Queue message send privilege is limited to `textvideo-jobs` where Azure RBAC
   scope allows, and Table data read/write is limited to `VideoJobs` where
   possible. Azure Table Data Contributor permits writes; document whether
   narrower custom permissions and table-level scope are feasible. Never assign
   subscription-wide Contributor, Owner, User Access Administrator, or paid
   provider permissions.
4. No unexpected role assignments inherited from subscription/resource group
   and no keys/secrets or paid FAL credentials. Do **not** grant roles during
   this readiness phase.

### Read-only Azure CLI evidence commands (operator executed, not yet run)

Run in an authenticated **read-only** Azure Cloud Shell session. Confirm the
subscription and resource group before commands. Store redacted outputs in an
approved private evidence location, not in public GitHub comments.

```bash
set -euo pipefail
RG=rg-textvideo-dev
IDENTITY_NAME=id-textvideo-dispatch-reconcile-dev
az account show --query '{subscription:id,tenant:tenantId}' -o json
az identity show -g "$RG" -n "$IDENTITY_NAME" --query   '{name:name,id:id,clientId:clientId,principalId:principalId,location:location}' -o json
PRINCIPAL_ID=$(az identity show -g "$RG" -n "$IDENTITY_NAME" --query principalId -o tsv)
az role assignment list --assignee "$PRINCIPAL_ID" --all   --query '[].{role:roleDefinitionName,scope:scope,principalType:principalType}' -o json
az containerapp env show -g "$RG" -n cae-textvideo-dev   --query '{name:name,location:location,workloadProfiles:properties.workloadProfiles,appLogsConfiguration:properties.appLogsConfiguration.destination}' -o json
az acr show -g "$RG" -n acrtextvideo1794671698   --query '{name:name,loginServer:loginServer,adminUserEnabled:adminUserEnabled}' -o json
az monitor log-analytics workspace show -g "$RG" -n log-textvideo-dev   --query '{name:name,location:location,customerId:customerId,retention:retentionInDays,sku:sku.name}' -o json
az monitor action-group list -g "$RG" --query   '[].{name:name,id:id,enabled:enabled}' -o json
```

The identity name above is **a proposed name**, not evidence of an existing
Azure identity. A read-only `ResourceNotFound` result is a blocker, not a
reason to create it without separate authorization.

## Image integrity — unverified

- `workerImage` is a required deployment parameter, but the Bicep file does
  not itself enforce its SHA-256 syntax.
- Offline validator accepts only the expected ACR registry and
  `repository@sha256:<64 lowercase hex>`; mutable tags and external
  registries fail validation.
- Before approval, record the exact image **digest**, confirm it exists in
  `acrtextvideo1794671698`, inspect provenance and vulnerability results,
  and compare running digest/CI artifact. No `:latest` or mutable tag.

## Schedule and cost guardrails — design only

- UTC 5-field cron; approved values `*/15 * * * *` and `0 * * * *`.
  The default every 15 minutes is **96 executions/day**, about **2,880 in
  a 30-day month**. The hourly option is 24/day.
- One replica, zero retries, 300-second hard replica timeout,
  0.5 vCPU and 1 GiB memory. A pessimistic 30-day maximum at 96 runs/day
  and 300s/run is 240 container-hours = 120 vCPU-hours and
  240 GiB-hours, before platform rounding, storage, registry, Log Analytics,
  query alerts, action-group delivery, and any other services.
- These are **usage ceilings from the template**, not a price quote or a hard
  Azure billing cap. Confirm the current region/plan pricing, actual measured
  execution time, budgets with alerts, and cost-center ownership.
- Setting `activateReconciliation=false` does not make a **deployed**
  scheduled job free: its `--check` command would still run on the schedule.
  Nothing should be deployed merely to exercise the preflight.
- The one-shot Python reconciler caps each invocation at 20 pages × 25 jobs
  by default, fails closed with a flag, and never calls the paid provider.
  Explicitly review storage and queue transaction charges at scale.

## Log query and Action Group — not verified in live Azure

The alert template uses `ContainerAppConsoleLogs_CL.Log_s` and the marker
`dispatch_reconciliation_requires_attention`. Microsoft documentation
identifies this custom table/column for direct Log Analytics routing;
resource-specific Azure Monitor routing instead uses
`ContainerAppConsoleLogs.Log`. Current `infra/main.bicep` describes a
Log Analytics destination, but deployed configuration may differ.

Use **read-only** Log Analytics queries after obtaining workspace ID
and ensuring a harmless test signal is present:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| where Log_s contains "dispatch_reconciliation"
| project TimeGenerated, ContainerAppName_s, Log_s
| take 20
```

Alternatively query `ContainerAppConsoleLogs` and `Log` if the workspace
uses the Azure Monitor destination. Only one schema should be used in
the deployed alert after verifying actual log rows and resource isolation.

The Action Group is an **unresolved required parameter**. Confirm it exists,
is enabled, belongs to the approved subscription, has an authorized
notification target, and actually delivers a test without exposing secrets.
The `activateAlerts=false` default prevents alert-resource creation.

### Remaining alert gaps

- Alert delivery has not been tested with an actual log entry.
- Missing-sweep/heartbeat and query-ingestion outages are not detected by
  the existing anomaly-marker alert.
- Confirm log retention, ingestion cost, query pricing, alert frequency,
  notification destination and budgeting before activating.

## Release decision

**BLOCKED** until (1) read-only live IAM/RBAC evidence, (2) digest-pinned
image provenance, (3) cloud log schema and routing validation, (4) Action
Group test, (5) cost approval and monitoring budget, and (6) explicit
owner risk acceptance on exact PR head are recorded. No Azure write RBAC,
deployment, scheduler activation, paid FAL, or security-sensitive merge.

## November 16 safe-integration staging changes (review-only)

This new **separate** draft staging branch is rebased on the main branch following PR #39; original PR #28 remains draft and unmerged. It changes the scheduled job blueprint to create **no Azure Container Apps scheduled reconciliation job at all when `activateReconciliation=false`** (rather than creating an hourly/daily billable preflight). If approved in a future security-sensitive merge, activation still requires a new independent owner authorization, dedicated identity, scoped queue/table/ACR privileges, reviewed immutable image, live log schema, Action Group test, and cost budget. Default cron on activation is hourly (24 executions/day), not every 15 minutes (96/day). With 300-second timeout this template's worst-case hourly execution window is 60 container-hours per 30-day month, 30 vCPU-hours and 60 GiB-hours, plus overhead. It is NOT a hard spend cap; price/metering remain unverified. No Azure resource was created, scheduled, or run during this review.

**Additional unresolved gates:** No evidence that `id-textvideo-dispatch-reconcile-dev` actually exists; ACR pull and OCI provenance unverified; Log Analytics custom-table schema (`ContainerAppConsoleLogs_CL`) may not match the deployed workspace; Action Group resource ID/delivery unconfirmed. Treat all as blockers, not as reasons for automatic IAM grants. The 2026-11-17 Azure what-if run 44000078189 was for the existing worker digest and failed at an undiagnosed stage, not the scheduled reconciler.

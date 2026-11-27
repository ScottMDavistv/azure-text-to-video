# Isolated ARM what-if — allowlisted Azure error diagnosis

**Status:** Development-only classification prepared; live error category **unverified**.
Source: manual main workflow run
[44002298041](https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/44002298041)
and sanitized artifact
[14292721764](https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/44002298041/artifacts/14292721764).
The authenticated Reader identity passed OIDC, account, resource-read and disabled
worker-baseline checks. The ARM `deployment group what-if` operation failed with
a generic error at stage `arm-whatif-preview`.

## Development-only safeguard

`scripts/azure_worker_whatif.py` now examines **only** the exact Azure CLI
leading error-code token or the top-level JSON `error.code` envelope in
captured stderr. The complete original stderr/stdout remains private in
process memory and is never logged, printed, persisted or uploaded. Unknown
or ambiguous codes fail closed. Categories in the sanitized artifact are:

| Sanitized category | Eligible source codes | Meaning |
|---|---|---|
| `rbac-denied` | `AuthorizationFailed`, `LinkedAuthorizationFailed` | Azure returned a recognizable denial. **Does not prove which permission/scope is missing.** |
| `template-validation` | `InvalidTemplate`, `InvalidTemplateDeployment`, `InvalidDeploymentParameter`, `BicepError` | Azure/CLI rejected a template, parameters, or compilation. Investigate non-IAM cause. |
| `cli-unavailable` | subprocess missing/timeout | Tool unavailable; no basis for permissions changes. |
| `unknown` | every other response, arbitrary text, malformed or oversized codes | No attribution is safe. |
| `not-classified` | other workflow stages or successful what-if | Not an ARM error classification. |

The error category is **not** permission to deploy, enable workers, grant Azure
roles, activate FAL, switch OIDC identities or auto-dispatch workflows. Strict
image-only diff verification is still required for successful previews.

## Exact next workflow and conditional RBAC procedure

1. Obtain supervised security-sensitive PR merge approval after exact-head
   CI. Keep this PR draft until that explicit approval; do not edit `main`
   directly.
2. Owner manually dispatches **Manual Azure Worker What-If (No Deployment)**
   on protected main. No inputs, Azure writes or IAM changes.
3. Retrieve the `azure-worker-whatif-evidence` JSON and examine
   `diagnostic_stage`, `arm_error_category`, `state` and `finding`.
4. **Only if** stage is `arm-whatif-preview` and category is
   `rbac-denied`: prepare a **proposal only** for an Azure operator to
   inspect the exact denied action, scope and role assignments through an
   approved private/read-only channel. Consider the possibility that
   Microsoft.Resources/deployments/whatIf/action is withheld from the
   existing Reader identity; do not assume this is the sole necessary
   permission. Prefer a dedicated scoped preview identity over elevating
   the general Reader audit role. Evaluate the exact action list and role
   assignment scope with Azure security before requesting approval.
5. No role assignment or identity alteration without a **new explicit owner
   IAM authorization**. A future separately authorized deployment needs its
   own what-if, full diff/security review, rollback, budget/alerts and
   deployment approval.
6. If category is `template-validation`, propose a tested non-IAM Bicep fix.
   If `unknown` or `cli-unavailable`, investigate in a restricted private
   diagnostic process; do not expand the output allowlist by searching
   narrative text for `AuthorizationFailed`.

## Release hold

This integration candidate is rooted at draft PR #44. PR #47 remains open
and draft until Scott reviews the evidence and separately decides on closure.
This document does not assert the current disposition of other PRs. No Azure deployment, write RBAC, new scheduled execution, paid
FAL calls or automatic workflow dispatch are authorized by this development
task. The exact source-tag-to-digest mapping has been verified through ACR
metadata only; provenance, worker pull, retention/rollback, monitoring and
cost-readiness still require independent evidence.

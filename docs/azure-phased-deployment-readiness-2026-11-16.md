# Phased Azure dev deployment readiness — security evidence and decision gates

Date: 2026-11-16. Scope: `rg-textvideo-dev` in subscription `41fbffdc-1793-44f9-b70f-220ea8a9bc6c`. **Audit/proposal only; no deployment authorization.**

## Status and evidence confidence

| Control | Current observation | Proof / limitation | Gate |
|---|---|---|---|
| PR #24 | Draft/unmerged at `c923bfb9c5584a1a0f38c2b0bafb460e3b9dd69c` | GitHub connector PR metadata; exact-head Backend/Security CI passed in preceding review | Keep unmerged |
| Existing broad foundation deployment | `main:.github/workflows/deploy-infrastructure.yml` retains manually dispatched `az deployment group create ... infra/main.bicep`, environment `dev-deployment` | Direct source inspection | **BLOCKER:** must be retired before any Azure write credential is granted |
| Preview broad foundation | `main:.github/workflows/preview-infrastructure.yml` uses `AZURE_CLIENT_ID`, full `infra/main.bicep` what-if | Direct source inspection | Do not use for targeted worker readiness |
| Backend image publisher | `main:.github/workflows/publish-backend.yml` pushes to ACR under `dev-deployment` using legacy `AZURE_CLIENT_ID` | Direct source inspection | Audit/retire legacy credential and ACR push route before write RBAC |
| Reader-only audit | `main:.github/workflows/azure-readonly-operations.yml` contains OIDC immutable repo identity and subject validation, read-only Azure collector, mock tests and artifact output; requires `AZURE_AUDIT_OPERATIONS_ENABLED=true`, environment `azure-readonly-audit` | Workflow code inspection; **no live run observed during this review** | Verify audit environment, its federation, variables and successful run; never assume `dev-deployment` variables are inherited |
| Manual isolated worker what-if | `main:.github/workflows/azure-worker-whatif.yml` checks out pinned PR #37 template, uses Reader OIDC identity and classifies only an image-digest delta | Workflow and `scripts/azure_worker_whatif.py` reviewed; **no live what-if executed in this review** | Expected ARM what-if authorization denial under Reader is a valid blocked finding. No IAM auto-escalation |
| GitHub preview and apply env | User supplied settings screenshots show `dev-deployment` and `dev-deployment-apply` restricted to `main`; owner reports variables added | Not API-verifiable with connected GitHub app; no native required reviewer configured | Validate via OIDC login and GitHub settings before any activation |
| Federated credentials | Owner executed bootstrap and reported `github-dev-preview`, `github-dev-apply`, `github-stage-b-audit` preserved | Owner Cloud Shell output, not direct Azure API in this review | Independently compare **immutable** issuer/subject/audience and principal IDs |
| RBAC | Owner output shows Reader on `id-textvideo-github-audit-dev` at dev RG and **zero** grant on deployment identity | Historical Azure audit, cannot establish current drift | Re-read inherited and direct roles before approval; no assignment now |
| Image provenance | Worker tag exists in previous inspection; worker what-if proposes pinned digest `sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646` | Repository script constant; ACR manifest **not** independently verified in this review | Confirm manifest exists, signature/provenance, and worker AcrPull |
| Worker safety | Existing worker intended manual, `worker_runtime.py --check`, no secrets, provider disabled, no queue consumption | Template and code; actual live drift not verified | Read-only audit and post-deployment check required |
| Alerts / budget | Prior operator reported action group, five alerts, $50/month budget; queue backlog alert failure | Owner historical reports, no fresh Azure evidence | Verify alerts actually fire, budget notifications, no auto-billable resources |

## Mandatory read-only OIDC verification

1. Verify the `dev-deployment` audit/preview identity federation has issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`, and exact subject `repo:ScottMDavistv@212811615/azure-text-to-video@1457187422:environment:dev-deployment`. Verify `dev-deployment-apply` federation has identical immutable repository identifiers and suffix `environment:dev-deployment-apply`. Preserve `stage-b-audit`. The dedicated `azure-readonly-audit` environment uses a **different** subject; do not reuse these federations interchangeably.
2. Inspect identity direct and inherited role assignments at the RG, subscription, and resource levels. Audit identity must not have write capabilities; deployment identity must still have **none**. Review legacy `AZURE_CLIENT_ID` identity separately.
3. Prefer already-existing read-only audit workflow only after checking its independent `azure-readonly-audit` environment and federation; `workflow_dispatch` requires separate operator authorization and is not part of this phase.
4. Save only sanitized run ID, full commit, identity type, audience/subject match, scope and status. Never write raw JWT, credentials, tenant secrets, or unredacted ARM responses to artifacts.
5. A passing mock test or GitHub variable screenshot does **not** demonstrate successful live token exchange.

## Least-privilege RBAC proposal — NOT APPROVED, NO GRANT

**Preferred safe baseline:** no Azure deployment role yet. The worker job is `Microsoft.App/jobs/job-textvideo-worker-dev`. ARM group what-if requires deployment-level and target-resource permissions; exact minimum Azure operations must be determined from ARM permission errors, verified against Azure documentation and tested with a temporary non-billable scope. Do not add Contributor/Owner at subscription, resource group, or service scope as a convenience.

**Candidate custom role for future supervised worker maintenance (subject to Azure evaluation):**
- Limit assignable scope and actual assignment as narrowly as ARM supports, ideally to the existing worker resource for worker update action; Azure deployment operations may require an RG-scoped permission to `Microsoft.Resources/deployments/*` at RG and separate worker-target write scope.
- Candidate management actions: `Microsoft.App/jobs/read`, `Microsoft.App/jobs/write` and only deployment read/write/what-if/validate operations that Azure requires (confirm exact provider operation strings using `az provider operation show`). Add prerequisite reads only after observed denial and review.
- Deny/omit `Microsoft.Authorization/*`, `Microsoft.ManagedIdentity/userAssignedIdentities/assign/action` unless independently proven necessary, `Microsoft.App/jobs/start/action`, Container Apps jobs secrets access, ACR push, Key Vault secret operations, role assignments, storage data writes, billing and subscription-wide access.
- Azure RBAC custom roles are **allowlists**, not explicit Deny controls. They do not enforce allowed image digest, FAL settings, number of resources, or per-commit approvals. Enforce these via code, policy, and operator review.
- If deployment cannot avoid identity assignment permission or broader access, **stop** and request separate owner approval with an exact permission trace. Do not grant automatically.

## Deployment scope and rollback checklist

1. Confirm protected `main` and exact-head required CI, threat review and signed-off template. PR #24 must remain draft until security independently verified.
2. Retire `main` legacy broad foundation apply and audit legacy OIDC/ACR push first. Distinct apply identity must **not** be available through a preview environment. The missing native GitHub Required reviewers control means environment branch protection is **not** an approval gate.
3. Freeze deployment scope to one existing **development** worker job, one immutable ACR digest, one manually triggered operation. No worker run, polling, FAL key, paid provider, auto-dispatch or production scope.
4. Confirm worker current full configuration and existing image manifest/digest in ACR; export a sanitized baseline configuration and pre-change resource ID/ETag (do not store secrets). Document preexisting working image reference and a reviewed, digest-pinned rollback template.
5. Review ARM full-resource what-if and reject any unknown `Create`, `Delete`, identity, environment, ingress, secret, registry, billing or non-image property changes. `ResourceIdOnly` is **not** sufficient to prove image-only safety.
6. Capture alert/action group and budget readiness, queue backlog monitoring, ACR AcrPull entitlement and registry retention. Cost guardrails and log delivery must be tested without paid calls.
7. After separate explicit approval (not given here), use **manual** apply only, controlled concurrency, postchecks and an abort/rollback decision. Rollback may itself cause an Azure write and therefore needs owner approval.
8. Fail closed on denied what-if or missing roles. No unattended remediation or IAM escalation.

## Prepared, not executed: supervised dev what-if

- Candidate: [Manual Azure Worker What-If](../.github/workflows/azure-worker-whatif.yml) for PR #37 digest-only candidate, **only after** read-only OIDC and template preflight evidence is verified. It is dispatch-only on `main`, does not deploy, and intentionally uses a Reader identity, so permission denial is a likely non-mutating outcome.
- Alternative: PR #24 introduces protected `dev-deployment-apply` what-if using the dedicated deployment identity; it is **not active** while PR #24 remains unmerged and the identity has no RBAC. Do not authorize it on current facts.
- Disposition: **no workflow dispatch** and **no Azure ARM what-if** in this phase. Owner must approve specific workflow, identity, exact SHA, scope, and allowed permission changes before a live run.

## Required owner decisions (not yet requested)

A. Approve **manual read-only audit run only** after `azure-readonly-audit` environment/OIDC trust has been verified. No Azure IAM changes.
B. After evidence, separately approve an exact minimum-permissions RBAC assignment **or reject it**, with a scope/role/action trace.
C. After A and B, decide on an exact-SHA manual what-if; actual apply is a further decision.

This is a readiness document, **not** authorization to merge PR #24, deploy anything, alter IAM/finance, activate FAL or enable automatic dispatch.

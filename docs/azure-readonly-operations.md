# Azure Read-Only Operations — Video Render Tool

Development-only GitHub Actions preflight. **Not permission to deploy or activate paid FAL**.

## Execution

Workflow: `.github/workflows/azure-readonly-operations.yml`.

- On workflow changes or script/test changes in pull requests, only offline mocked tests run; **never** run Azure OIDC on untrusted PR code.
- On `main`, run after related script/workflow pushes, every day at **13:41 UTC**, or via supervised `workflow_dispatch`.
- The Azure job uses GitHub Environment `dev-deployment` (restricted to `main`) and existing Reader-only managed identity `id-textvideo-github-audit-dev`. GitHub OIDC is the only auth path; no client secret, PAT, privileged GitHub token, or deploy identity.
- Requires `AZURE_PREVIEW_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` environment/repository variables previously configured by the owner. The workflow checks the known dev subscription and dedicated preview client ID before login. No new variables or RBAC grants are needed.
- To inspect a run: GitHub **Actions → Azure Read-Only Operations → latest main-branch run**. Open job summary and download `azure-readonly-operations` artifact (JSON and Markdown, 14-day retention). **Only sanitized, allowlisted fields are published**, without raw Azure responses, tenant IDs, principal IDs, access tokens, prompt text, environment secret values, or FAL credentials.
- No issue creation, external API, deployment, worker execution, ACR push, queue dispatch, auto-merge or Azure configuration mutation.

## Inspected controls

The script uses only Azure account/ARM resource/managed identity/ACR metadata and role-assignment LIST operations:
- Correct development subscription/resource group and existing manual worker job.
- User-assigned worker identity and matching managed identity for ACR pulls.
- Existing registry hostname, container image identity and **SHA-256 digest format**. A mutable tag is **BLOCKED** as a deployment readiness gate (the current worker image is tagged).
- Manual trigger, Consumption profile, inspection-only command/arguments.
- Disabled provider, consumption and live-render flags; no configured worker secrets. Missing `ENABLE_PAID_FAL_WORKER` is accepted as disabled.
- Distinct GitHub Reader/deploy identities; Reader role on dev RG, and absence of RG/inherited roles for deployment identity.

A fully green report is **preflight-only**, never deployment permission. Failure to read required Azure data fails the workflow with a sanitized error. Findings that are unsafe generate a report with `deployment_readiness=blocked` so evidence remains accessible; operators must not treat a green workflow run as readiness.

## Explicit blind spots / owner gates

- Reader-only RG access does not necessarily enumerate child-resource-specific RBAC assignments, other subscriptions or tenants. Do not assert global absence of write privileges from this report.
- Parsing a SHA-256 image reference does not prove ACR digest existence, signature/provenance or actual pull entitlement. Those require separate read-only or approved access checks.
- GitHub environment protection, federation subjects/audiences and live Azure OIDC authentication must be validated separately; a successful main run proves OIDC worked for that run, not that all security policy is correct.
- ARM what-if may require write-capable permissions; it is **not** part of this Reader job. Before PR #24/#28 activation, obtain separately reviewed what-if evidence, immutable image approval, actual least-privilege RBAC and owner deployment authorization.
- No runtime job health, executions, FAL API, scheduled reconciliation activation or Azure Monitor alert delivery is inferred from a successful inventory.
- GitHub Actions schedule can be delayed or disabled. Verify a real successful main run; a PR CI pass only proves offline tests.
- Keep `AZURE_DEV_DEPLOY_ENABLED` false/unset, Azure write RBAC unassigned and paid FAL disabled until independent security gating and explicit deployment authorization.

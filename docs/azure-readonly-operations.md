# Azure Read-Only Operations — Video Render Tool

Development-only GitHub Actions preflight. **Not permission to deploy or activate paid FAL**.

## Execution

Workflow: `.github/workflows/azure-readonly-operations.yml`.

- On workflow changes or script/test changes in pull requests, only offline mocked tests run; **never** run Azure OIDC on untrusted PR code.
- On `main`, run after related script/workflow pushes, every day at **13:41 UTC**, or via supervised `workflow_dispatch`.
- The Azure job uses dedicated GitHub Environment `azure-readonly-audit` (must be created and restricted to `main`) and existing Reader-only managed identity `id-textvideo-github-audit-dev`. GitHub OIDC is the only auth path; no client secret, PAT, privileged GitHub token, or deploy identity.
- Requires `AZURE_AUDIT_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` variables on the dedicated audit environment. Set the audit client ID to the existing Reader identity; no client secret or new RBAC grant is needed. The exact identifiers are supplied through environment variables, not hard-coded in source. **Activation is gated by `AZURE_AUDIT_OPERATIONS_ENABLED=true`; leave false/unset until the setup below is verified.**
- To inspect a run: GitHub **Actions → Azure Read-Only Operations → latest main-branch run**. Open job summary and download `azure-readonly-operations` artifact (JSON and Markdown, 14-day retention). **Only sanitized, allowlisted fields are published**, without raw Azure responses, tenant IDs, principal IDs, access tokens, prompt text, environment secret values, or FAL credentials.
## Mandatory setup before enabling (owner action, not performed by this PR)

1. In GitHub repository Settings → Environments create **`azure-readonly-audit`**. Allow only protected `main`; avoid required human reviewers if unattended daily audits are intended. Do not reuse `dev-deployment` or `dev-deployment-apply`.
2. Add environment variables `AZURE_AUDIT_CLIENT_ID` (existing audit Reader client ID), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Do not put any secrets in variables.
3. Independently review and **explicitly authorize** a new federated credential for `id-textvideo-github-audit-dev`, binding ONLY immutable subject `repo:ScottMDavistv@212811615/azure-text-to-video@1457187422:environment:azure-readonly-audit`, issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`. This is a separate Azure IAM write and is **not** performed by this PR. Do not add a credential to the deploy identity.
4. Evaluate all existing Azure federation credentials on both managed identities; Azure trusts the OIDC subject, not a workflow file or client-id variable. The existing `dev-deployment` credential on the audit Reader identity allows other workflows using that environment to request Reader tokens. If strict per-environment isolation is required, remove that former credential **only after** reviewing dependencies and authorizing the IAM change separately. A new audit environment alone does not revoke older trust paths. The deployment identity's `dev-deployment-apply` credential remains unchanged.
5. After PR review and merge, set `AZURE_AUDIT_OPERATIONS_ENABLED=true` at the repository level (the job has a fail-closed gate); trigger a read-only `main` run and review successful OIDC login, report contents and role scoping. No Azure deployment or paid provider activation.

- No issue creation, external API, deployment, worker execution, ACR push, queue dispatch, auto-merge or Azure configuration mutation.

## Inspected controls

The script uses only Azure account/ARM resource/managed identity/ACR metadata and role-assignment LIST operations:
- Correct development subscription/resource group and existing manual worker job.
- User-assigned worker identity and matching managed identity for ACR pulls.
- Existing registry hostname, container image identity and **SHA-256 digest format**. A mutable tag is **BLOCKED** as a deployment readiness gate (the current worker image is tagged).
- Manual trigger, Consumption profile, inspection-only command/arguments.
- Disabled provider, consumption and live-render flags; no configured worker secrets. Missing `ENABLE_PAID_FAL_WORKER` is accepted as disabled.
- Distinct GitHub Reader/deploy identities; exactly Reader **at the dev RG scope** (subscription-inherited Reader is blocked), and absence of RG/inherited roles for deployment identity.

A fully green report is **preflight-only**, never deployment permission. Failure to read required Azure data fails the workflow with a sanitized error. Findings that are unsafe generate a report with `deployment_readiness=blocked` so evidence remains accessible; operators must not treat a green workflow run as readiness.

## Explicit blind spots / owner gates

- Reader-only RG access does not necessarily enumerate child-resource-specific RBAC assignments, other subscriptions or tenants. Preflight actively fails a Reader assignment inherited from subscription scope. Do not assert global absence of write privileges from this report.
- Parsing a SHA-256 image reference does not prove ACR digest existence, signature/provenance or actual pull entitlement. Those require separate read-only or approved access checks.
- GitHub environment protection, federation subjects/audiences and live Azure OIDC authentication must be validated separately; a successful main run proves OIDC worked for that run, not that all security policy is correct. **OIDC tokens are scoped to environments, not individual workflow files; repository controls remain essential.**
- ARM what-if may require write-capable permissions; it is **not** part of this Reader job. Before PR #24/#28 activation, obtain separately reviewed what-if evidence, immutable image approval, actual least-privilege RBAC and owner deployment authorization.
- No runtime job health, executions, FAL API, scheduled reconciliation activation or Azure Monitor alert delivery is inferred from a successful inventory.
- GitHub Actions schedule can be delayed or disabled. Verify a real successful main run; a PR CI pass only proves offline tests.
- Keep `AZURE_DEV_DEPLOY_ENABLED` false/unset, Azure write RBAC unassigned and paid FAL disabled until independent security gating and explicit deployment authorization.

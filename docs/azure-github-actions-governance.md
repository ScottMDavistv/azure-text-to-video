# GitHub Actions Azure deployment governance — November 16, 2026

## Read-only audit findings
- Repository: `ScottMDavistv/azure-text-to-video`; default branch `main`.
- Active branch ruleset `Protect main - Backend Tests` (ID 27328702) requires `Backend tests (Python 3.12)` and `Security checks (Python 3.12)`, PRs, non-fast-forward/deletion protection; **zero** required approving reviews. No bypass actors reported. This is single-owner governance, not two-person review.
- Existing OIDC consumers: `test-azure-oidc.yml`, `preview-infrastructure.yml`, `deploy-infrastructure.yml`, `publish-backend.yml`. They request `id-token: write` and read `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` through GitHub Actions variables; actual variable values are not readable via the current connector.
- Existing `dev-deployment` environment is referenced in workflow code; **its actual existence, required reviewers, allowed branches, protection rules, and secret/variable scopes could not be read** with the connected GitHub permissions.
- Azure previously showed a `Contributor` principal with additional ACR push access, and a separate `id-textvideo-github-audit-dev` Reader principal; the OIDC client ID mapped to those principals remains **unverified**. Do not assume the Reader identity can deploy.
- The existing broad `deploy-infrastructure.yml` would apply `infra/main.bicep`, whose latest user-reported what-if proposed four existing resource modifications. This PR disables that entry point.
- Azure credential values, GitHub secrets inventory, federated identity credential subjects, tenant trust, and Azure role assignments for the actual OIDC client are **not verified** through this GitHub connection. Do not assume absent credentials or safe trust configuration.

## Gate activation checklist — owner administration
1. Configure GitHub Environment `dev-deployment-apply` with a protected deployment branch (`main` only), required reviewer approval if supported, and **prevent self-review** if another eligible approver exists. As this project has a single owner, use manual dispatch plus the confirmation and enabled-variable gates instead of asserting a nonexistent independent human review.
2. Set environment variables `AZURE_PREVIEW_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` in `dev-deployment` (preview), and `AZURE_DEPLOY_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` in `dev-deployment-apply` (write) to **distinct** Azure identities. Confirm the two client IDs differ. Never use the write identity in the preview environment. Use a Reader + what-if-capable identity for preview and least-privilege deployment identity for apply; grant only required resource-group/resource roles, not subscription Owner. Never put Azure client secrets in GitHub.
3. Verify Azure federated credential issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`, and subject `repo:ScottMDavistv/azure-text-to-video:environment:dev-deployment-apply` for write identity. Preview identity subject should match its own environment; inspect the actual federated credential records in Azure before enabling. Verify tenant and subscription IDs against the approved development scope.
4. Preserve ruleset 27328702, current required checks, protected main, and single-owner security governance. Workflow mutations require PR, tests, and owner review of the proposed diff.
5. Confirm Azure policy, billing, and RBAC drift. Confirm `id-textvideo-worker-dev` has only approved worker permissions, paid FAL is not configured, and all existing disabled worker flags remain false.
6. Only after checks pass, set repository/environment variable `AZURE_DEV_DEPLOY_ENABLED=true` and `PROTECTED_MAIN_VERIFIED=true`. Keep these **unset or false** until manual inspection is complete.
7. Read-only previews are triggered automatically on relevant `main` commits and daily at 13:23 UTC, and can also be dispatched manually. Verify the preview identity works without write permissions. Trigger `Guarded Azure Dev Worker Deployment` on `main` with `operation=preview` first. Review what-if. For apply, select `operation=deploy`, confirmation `DEPLOY-DEV-WORKER`; the separate `dev-deployment-apply` environment gates execution.
8. Confirm post-deployment verification and GitHub job evidence before marking the operation complete. This workflow only deploys `infra/worker-job.bicep` with the pinned existing worker image. It does not execute jobs or enable FAL.

## Current limitations and future work
- No GitHub environment, secrets, variables, federated credential, or Azure RBAC mutation was performed.
- Existing OIDC-authenticated workflows remain in the repository; their Azure permissions must be independently verified. This PR does not claim they are safe for unattended execution.
- Automatic **read-only previews** run on relevant `main` pushes and a daily schedule. No automatic deploy on `push`, `schedule`, `issue`, `workflow_run`, or agent dispatch is granted. Writes remain opt-in and guarded.
- This workflow is for inspection-only worker maintenance, **not** queue processing or backlog alert repair. A separate resource-scoped monitoring workflow should be reviewed before deployment.
- Maintain evidence of exact commit, workflow run, Azure what-if, actual resource changes, and postchecks. Stop if any gate fails.

## FAL exclusion
Never configure `FAL_KEY`, FAL Key Vault references, billable rendering, automatic queue consumption, FAL provider activation, or FAL-model invocation from these workflows.

## Owner decision record (November 16, 2026)
- Identity strategy: distinct read-only/preflight OIDC identity and least-privilege deployment identity.
- Deployment strategy: automatic previews, supervised modifications only.
- Merge policy: keep PR #24 draft and unmerged until Azure OIDC subject/audience/RBAC and GitHub environment controls are independently verified.
- No paid FAL access or activation.

## Remaining external verification
The GitHub connector cannot read or modify repository Environment configuration or Azure federated credential records; a repository workflow referencing a name is **not evidence** the environment exists or is protected. An owner must inspect [GitHub environments](https://github.com/ScottMDavistv/azure-text-to-video/settings/environments) and the Azure user-assigned identities' federated credential records. Both identities must be checked for distinct client IDs and resource scopes. If Azure custom-role read-only what-if permissions are insufficient, report this; do not grant subscription-wide Contributor merely to make previews pass.

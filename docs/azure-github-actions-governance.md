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
2. Set environment variables `AZURE_PREVIEW_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` in `dev-deployment` (preview), and `AZURE_DEPLOY_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` in `dev-deployment-apply` (write) to **distinct** Azure identities. Confirm the two client IDs differ. Never use the write identity in the preview environment. Use a strictly Reader identity for automatic inventory previews; ARM/Bicep what-if needs write-level resource/deployment permissions and therefore runs only using the protected deployment identity with `operation=whatif`. Use a separate least-privilege deployment identity for apply; grant only required resource-group/resource roles, not subscription Owner. Never put Azure client secrets in GitHub.
3. Verify Azure federated credential issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`, and subject `repo:ScottMDavistv/azure-text-to-video:environment:dev-deployment-apply` for write identity. Preview identity subject should match its own environment; inspect the actual federated credential records in Azure before enabling. Verify tenant and subscription IDs against the approved development scope.
4. Preserve ruleset 27328702, current required checks, protected main, and single-owner security governance. Workflow mutations require PR, tests, and owner review of the proposed diff.
5. Confirm Azure policy, billing, and RBAC drift. Confirm `id-textvideo-worker-dev` has only approved worker permissions, paid FAL is not configured, and all existing disabled worker flags remain false.
6. Only after checks pass, set repository/environment variable `AZURE_DEV_DEPLOY_ENABLED=true` and `PROTECTED_MAIN_VERIFIED=true`. Keep these **unset or false** until manual inspection is complete.
7. Read-only inventory previews are triggered automatically on relevant `main` commits and daily at 13:23 UTC, and can be dispatched manually. Verify the Reader identity works without write permissions. For ARM what-if, run `operation=whatif` on `main` through `dev-deployment-apply` with the separate deployment identity. Review the what-if output before a new dispatch using `operation=deploy` and confirmation `DEPLOY-DEV-WORKER`. The `dev-deployment-apply` environment gates both what-if and apply.
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

## Identity bootstrap staged (not executed in Azure)
- Existing audit identity: `id-textvideo-github-audit-dev`; preserve its `github-stage-b-audit` federated credential.
- Proposed additional preview credential: `github-dev-preview`, immutable GitHub subject `repo:ScottMDavistv@212811615/azure-text-to-video@1457187422:environment:dev-deployment`.
- Proposed separate deployment identity: `id-textvideo-github-deploy-dev`, credential `github-dev-apply`, immutable GitHub subject `repo:ScottMDavistv@212811615/azure-text-to-video@1457187422:environment:dev-deployment-apply`.
- Issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`.
- Bootstrap must verify Azure account and preexisting credentials, never overwrite the `stage-b-audit` trust, never grant write RBAC automatically, and must not merge PR #24.
- Azure documentation confirms ARM what-if requires the same resource write/deployment permission set as actual deployment. Do not misrepresent ARM what-if as guaranteed read-only RBAC. The operation itself is a preview, but the identity capable of running it has potentially dangerous capabilities.

## November 16, 2026 single-owner approval amendment (current)
- Owner confirmed `dev-deployment` and `dev-deployment-apply` exist, each restricted to `main`. Environment variables were added by the owner; the screenshot verified deploy client, subscription and tenant IDs in `dev-deployment-apply`. Their runtime values and successful GitHub OIDC login have **not** been tested.
- Owner Azure bootstrap output reports separate managed identity `id-textvideo-github-deploy-dev`, federations `github-dev-preview` and `github-dev-apply`, preview Reader at `rg-textvideo-dev`, **zero** deployment identity RBAC assignments, and preserved `github-stage-b-audit`. These are owner-provided results rather than direct Azure API verification.
- Native GitHub Required reviewers were **not available** to the owner. A protected environment with a main-only branch rule is **not** a human approval gate. Do not describe it as independently approving runs.
- Replacement control: only GitHub actor `ScottMDavistv` may dispatch `whatif` or `deploy`; `deploy` additionally requires full exact main commit SHA in `approved_sha`, confirmation `DEPLOY-DEV-WORKER`, `PROTECTED_MAIN_VERIFIED=true`, and `AZURE_DEV_DEPLOY_ENABLED=true`. A separate dispatch and review of what-if evidence is an operational procedure, **not a cryptographic link** between two runs.
- All workflow edits are subject to main branch checks, including `scripts/check_azure_deployment_governance.py` run by Security Checks. The static regression test is defense in depth, **not** proof that Azure OIDC, RBAC, or environment approvals enforce the intended policy.
- **Fail-closed operating state:** do not set either activation variable true and do not grant deployment write RBAC until independent verification of identities, Azure scopes, OIDC token audience/subjects, owner action controls, CI, and all relevant legacy workflows has been recorded. No approval to deploy follows from merely merging code.
- Existing deployed infrastructure is unchanged. No worker execution, paid provider enablement, or Azure deployment is allowed by this amendment.

## November 16 safe-integration amendment — not deployable

This separate draft integration branch is based on main after PR #39; it does **not** merge or activate PR #24. The previously mutable Git-SHA image references in preview/deploy code have been changed to the registry metadata-verified OCI digest from audit run 43983848946. Exact image trust, ACR pull, rollback retention, and ARM what-if are still unverified. The guarded deployment workflow remains unapproved and must not be merged merely because CI is green. No deployment identity has write RBAC. Existing main Azure worker what-if run 44000078189 failed safely; diagnostic PR #43 is pending review. Do not set `AZURE_DEV_DEPLOY_ENABLED` or `PROTECTED_MAIN_VERIFIED` true or grant RBAC. Legacy deployment retirement and independent OIDC permissions reviews remain required.

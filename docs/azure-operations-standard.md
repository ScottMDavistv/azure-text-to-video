# Standard Azure operations interface: GitHub Actions

**Policy for Video Render Tool**: perform routine Azure read-only inspections and evidence publication through the versioned `Azure Read-Only Operations` GitHub Actions workflow, not by repeatedly asking the owner to run Azure Cloud Shell commands. A green offline PR check is not evidence of a live Azure inspection.

## Standard operating cycle

1. Open **Actions → Azure Read-Only Operations** on the protected `main` branch; use its daily schedule or owner-initiated workflow dispatch. Workflow dispatch is not exposed by every GitHub integration. If automation cannot dispatch, use the scheduled run rather than asking for raw Azure CLI transcripts.
2. The audit job is available only after the separate `azure-readonly-audit` GitHub Environment, immutable-subject Reader OIDC federation and environment variables are configured, and repository variable `AZURE_AUDIT_OPERATIONS_ENABLED=true` is deliberately enabled. The default is **disabled**.
3. Collect evidence from the GitHub Actions **Reader-only Azure preflight** job summary and `azure-readonly-operations` Markdown/JSON artifact (14-day retention). Check the run branch, exact commit, completion state, and source of the report. Report no data as *unknown*, never as *pass*.
4. A report with `deployment_readiness=blocked` identifies a gate to investigate; `preflight-only` indicates only that this narrow static audit found no blockers. Neither state authorizes an Azure deployment, security-sensitive merge, RBAC grant, API activation, or FAL spending.
5. For a required Azure change, submit a separate reviewed PR with an immutable image, narrowly scoped permissions, a what-if and operator approval. The audit identity must never be given write access and audit workflow must never gain deployment functions.

## Protected workflows

- `Azure Read-Only Operations`: default interface for recurring development resource checks; read-only Reader identity, `azure-readonly-audit` environment, main-only, no third-party rendering.
- `Design Management - Twice Daily Audit`: independent GitHub project governance reporting; does **not** grant Azure deployment authority.
- `guarded-azure-worker-deploy` (PR #24): draft guarded worker deployment proposal; separate explicit approval and RBAC safeguards apply. Do not merge or activate based on this audit.
- `deploy-infrastructure`, `publish-backend`, `test-azure-oidc`: pre-existing legacy workflows; existing auth must be reviewed before use. No automatic action by the new audit workflow.

## Outstanding identity and migration controls

The user created dedicated `github-readonly-audit` federation for Reader-managed `id-textvideo-github-audit-dev`. Its older `github-dev-preview` credential still trusts `dev-deployment`. While the latter exists, those environment workflows **could potentially request Reader tokens** if granted OIDC; the new isolated audit workflow does not remove that alternate trust. The legacy `AZURE_CLIENT_ID` was identified as the separate `sp-textvideo-github-dev` application whose own federated subjects include `dev-deployment` and `main`. Following an explicit Azure IAM change approval and independent dependency check, remove obsolete `github-dev-preview` without deleting Stage B or the new audit credential. Do not claim strict federation isolation before then.

No Azure write RBAC assignment, paid FAL activation, production deployment, worker execution, automatic merge or dispatch is part of routine auditing.

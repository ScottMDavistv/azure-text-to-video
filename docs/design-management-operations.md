# Design Management Agent — operations and permissions

Status: **draft development branch only**, not active on default branch.

## Scope

- Scheduled audits at 02:00 and 14:00 UTC, twice daily. Mountain local times shift by one hour with daylight saving time. Delays are possible.
- Deterministic GitHub checks inspect main's current commit, source-file indicators, up to ten CI runs, open PRs, and create deduplicated critical release-gate candidate issues.
- The source-file check is **only a heuristic**. It must not certify missing functionality, readiness, or independently verified bugs.
- GitHub dashboard issue #16 is the reporting surface. Issue #18 holds the synthetic five-minute acceptance milestone.
- Optional Azure inventory uses only Reader privileges; records limited public resource metadata. No expensive Azure diagnostic queries and no secret retrieval.
- No LLM calls, FAL requests, Azure deployments, merges, or production changes.

## Azure opt-in (requires separate owner configuration)

1. Create/use a dedicated Microsoft Entra application or user-assigned identity with **Reader only** scoped to resource group `rg-textvideo-dev`. Do not grant Contributor, Owner, Key Vault Secrets User, Storage data access or billing permissions.
2. Configure a GitHub OIDC federated credential limited to `repo:ScottMDavistv/azure-text-to-video:ref:refs/heads/main`, audience `api://AzureADTokenExchange`. This setup is **not** carried out by this PR.
3. In GitHub repository Actions variables set:
   - `AZURE_AUDIT_ENABLED=true` only after verifying scope, identity and audit
   - `AZURE_AUDIT_CLIENT_ID` to the Reader app/identity client ID
   - `AZURE_AUDIT_TENANT_ID` to its tenant ID
   - `AZURE_AUDIT_SUBSCRIPTION_ID` to the target subscription UUID
   - `AZURE_AUDIT_RESOURCE_GROUP=rg-textvideo-dev`
4. On a default-branch schedule or a permitted `workflow_dispatch`, the audit job obtains OIDC credentials and executes `az resource list` for the named resource group only.
5. Review the produced `azure-inventory.json` artifact. The inventory shows resources and provisioning metadata; **it does not prove deployment health, ability to render, job scaling, cost, or security compliance**.
6. Leave `AZURE_AUDIT_ENABLED` unset/false until OIDC and Reader RBAC are tested.

## Critical issue controls

- Each critical candidate has a stable hidden `design-management-blocker:<key>` marker.
- Existing open AND closed issues are searched to avoid duplicate creation.
- The discovery check is intentionally conservative. If GitHub retrieval fails the audit aborts; never claim a clean result.
- Issue creation is restricted to the scheduled main-branch auditing job; PR test jobs have no issue-write permission.
- A candidate requires human validation before it is treated as a confirmed bug.
- The dashboard issue is updated in place between marker boundaries while preserving owner's manual notes.
- Concurrency is serialized for the scheduled job.

## 300-second non-billable milestone

`python scripts/synthetic_300s_render.py --duration 300 --segment 60` generates five synthetic clips with FFmpeg color visuals and sine-tone audio, joins them, and validates the total duration and both streams with ffprobe.

`.github/workflows/synthetic-300s-acceptance.yml` runs this without Azure credentials and with rendering/provider flags disabled, uploading the MP4 plus JSON evidence for manual inspection.

**Milestone limitation:** This is a media assembly fixture, not yet a full frontend → authenticated API → asynchronous worker → downloadable MP4 integration test. That integration remains an explicit checklist item in issue #18.

## Governance

Draft PR #17 does not activate scheduling. A separate authorized merge into main and any Azure OIDC provisioning are independent decisions. Preserve all existing single-owner security safeguards and prevent unreviewed paid provider activation.

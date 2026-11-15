# B2 — Workload identity and storage RBAC: implementation plan

## Confirmed from first live read-only inventory
- API `ca-textvideo-api-dev`: user-assigned `id-textvideo-api-dev`
- Worker `job-textvideo-worker-dev`: same user-assigned `id-textvideo-api-dev`
- Worker trigger: Manual
- Storage account: `sttextvideo1794671698` (TLS 1.2; ARM firewall default Allow)
- The initial account-scope audit returned no entries because roles were **child scoped**. Follow-up audit run https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43762795484 verified the shared runtime principal already has Storage Table Data Contributor on `VideoJobs`, Storage Queue Data Contributor on `textvideo-jobs`, and Storage Blob Data Contributor on `videos`. **Do not re-grant these roles.**

## Required design
Separate identities to prevent a compromised API from inheriting the worker's blob writing and provider job privileges:
- API: `id-textvideo-api-dev` (existing), access only to needed project/job tables and enqueue operation.
- Worker: new `id-textvideo-worker-dev` (not created), constrained to job/finance/provider tables, dequeue, and destination blob upload.
- Reconciliation: future dedicated `id-textvideo-reconciliation-dev` (not created), no FAL credentials.
- GitHub audit: `id-textvideo-github-audit-dev`, **Reader only**, not authorized to change IAM.

## Proposed Azure data-plane roles — administrator review before assignment

| Runtime | Resource | Role | Rationale |
|---|---|---|---|
| API | Project/job tables | Storage Table Data Contributor (table-scoped where practical) | Authorized project/job writes |
| API | Render queue | Storage Queue Data Message Sender (queue scope) | Send render jobs; no dequeue |
| Worker | Job / finance / provider-ledger tables | Storage Table Data Contributor (table or storage account scope when necessary) | Durable financial ETag transactions |
| Worker | Render queue | Storage Queue Data Message Processor (queue scope) | Poll / delete messages |
| Worker | Videos blob container | Storage Blob Data Contributor (container scope) | Persist video artifacts |
| Reconciliation | Selected tables and notification queue | Narrowest supporting Storage Table / Queue data roles | Non-billable recovery |

**Do not assign roles by this file alone.** Verify resource names, operations, and table-partition assumptions; RBAC scopes cannot isolate partitions in one Azure Table. The code currently uses `get_jobs_table_client()` for multiple functions; separate table ownership is a design change and must be tested before restricting access.

### Important safety note
Azure **Reader** does not grant Table/Queue/Blob data access. Azure **Contributor** does not itself grant OAuth data-plane access to Tables either; role assignments and effective scopes must be validated explicitly.

## Execution gates
1. **Complete:** `02-identity-rbac-audit.sh` verified existing child-scoped roles. Do not treat storage-account Reader results as proof of missing data access.
2. Review exact table, queue and blob names, and split worker identity.
3. Create/assign **new worker identity roles** using a separate approved administrator workflow or portal action. Safely reduce API's Queue role from Data Contributor to Message Sender and remove its Blob role only after the worker migration is tested. Do not grant Owner/Contributor to the GitHub audit identity.
4. Test token acquisition and non-billable Table/Queue/Blob operations with dedicated dev principals; ensure API cannot dequeue or upload video.
5. Keep worker Manual, `ENABLE_PAID_FAL_WORKER=false`, FAL credentials absent.

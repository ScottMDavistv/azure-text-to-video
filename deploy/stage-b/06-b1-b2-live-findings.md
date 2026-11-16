# Stage B1/B2 — read-only findings and rollout gates

## Verified with GitHub-to-Azure federation

Audit run: https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43762545166

- Microsoft Entra OIDC federation for the GitHub `stage-b-audit` environment works and logs in without a client secret.
- Existing API `ca-textvideo-api-dev` and worker `job-textvideo-worker-dev` both use the user-assigned managed identity `id-textvideo-api-dev`. Worker trigger is **Manual**.
- Storage account `sttextvideo1794671698` exposes:
  - Azure Table: `VideoJobs`
  - Azure Queue: `textvideo-jobs`
  - Blob container: `videos`
- Account-scope plus inherited role lookup for the shared API/worker principal returned no roles **at that scope**. A subsequent child-resource audit completed successfully: https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43762795484
- **Confirmed existing assignments for the shared runtime principal:** Storage Table Data Contributor on `VideoJobs` table, Storage Queue Data Contributor on `textvideo-jobs` queue, and Storage Blob Data Contributor on `videos` container. Do not duplicate these assignments; they exist at child-resource scopes.
- **Remaining security risk:** API and worker share the same user-assigned identity, so both currently inherit queue dequeue and blob write rights. Split them during an approved deployment before enabling paid processing.
- GitHub audit identity is Reader and cannot enumerate Entra application registrations using Microsoft Graph (`Insufficient privileges`). No user app registration was created or verified.
- Storage account ARM reported TLS 1.2 and default network action Allow. It did not explicitly report shared key disabled (null). A separate networking/security review remains necessary.

## Pending B1 (administrator)
1. Identify or create the tenant-only `Video Render Tool API (dev)` registration using `03-entra-registration-guide.md`.
2. Approve `access_as_user` delegated scope. Confirm actual access token `aud`, issuer and sign-in population.
3. Provision trusted external-user -> internal UUID mappings. Do not let users choose an administrator role from request JSON.
4. Run signed-token auth tests before considering enabling project routes.
5. The GitHub Reader identity must remain unchanged; directory administrator performs this step.

## Pending B2 (administrator)
1. **Complete:** Effective RBAC audit verified the three existing child-scoped data roles. Preserve these while planning identity separation.
2. Create and attach a **separate worker identity** with reviewed data-plane permissions.
3. After the identity split, replace the API's broad Queue Data Contributor capability with Message Sender and remove Blob Data Contributor from API; assign the new worker Table, Queue processor and Blob write roles. Scope grants per resource and review migrations carefully. Table RBAC is table scoped, not partition scoped.
4. Test data-plane access from the actual API and worker identity (not the GitHub audit identity) after assignments propagate.
5. Do not enable live FAL, switch worker trigger, deploy paid code or merge draft PRs.

### GitHub checkpoint
Stage A CI: 464 passed, 2 skipped (dedicated Azurite tests passed separately). Latest B1 configuration and B2 audit regression tests run in GitHub. Neither test suite is production permission verification.

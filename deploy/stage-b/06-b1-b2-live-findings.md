# Stage B1/B2 — read-only findings and rollout gates

## Verified with GitHub-to-Azure federation

Audit run: https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43762545166

- Microsoft Entra OIDC federation for the GitHub `stage-b-audit` environment works and logs in without a client secret.
- Existing API `ca-textvideo-api-dev` and worker `job-textvideo-worker-dev` both use the user-assigned managed identity `id-textvideo-api-dev`. Worker trigger is **Manual**.
- Storage account `sttextvideo1794671698` exposes:
  - Azure Table: `VideoJobs`
  - Azure Queue: `textvideo-jobs`
  - Blob container: `videos`
- Account-scope plus inherited role lookup for the shared API/worker principal returned **no RBAC assignments**. Do not conclude child-scoped roles are missing until additional table/queue/container audit passes.
- GitHub audit identity is Reader and cannot enumerate Entra application registrations using Microsoft Graph (`Insufficient privileges`). No user app registration was created or verified.
- Storage account ARM reported TLS 1.2 and default network action Allow. It did not explicitly report shared key disabled (null). A separate networking/security review remains necessary.

## Pending B1 (administrator)
1. Identify or create the tenant-only `Video Render Tool API (dev)` registration using `03-entra-registration-guide.md`.
2. Approve `access_as_user` delegated scope. Confirm actual access token `aud`, issuer and sign-in population.
3. Provision trusted external-user -> internal UUID mappings. Do not let users choose an administrator role from request JSON.
4. Run signed-token auth tests before considering enabling project routes.
5. The GitHub Reader identity must remain unchanged; directory administrator performs this step.

## Pending B2 (administrator)
1. Complete effective RBAC audit at actual Table/Queue/Blob child scopes.
2. Create and attach a **separate worker identity** with reviewed data-plane permissions.
3. Assign minimal API Table and Queue sender roles; worker Table, Queue processor and Blob write roles only after an explicit operations/security review. Table RBAC is table scoped, not partition scoped.
4. Test data-plane access from the actual API and worker identity (not the GitHub audit identity) after assignments propagate.
5. Do not enable live FAL, switch worker trigger, deploy paid code or merge draft PRs.

### GitHub checkpoint
Stage A CI: 464 passed, 2 skipped (dedicated Azurite tests passed separately). Latest B1 configuration and B2 audit regression tests run in GitHub. Neither test suite is production permission verification.

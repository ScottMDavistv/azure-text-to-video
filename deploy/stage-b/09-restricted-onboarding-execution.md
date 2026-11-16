# B1 — Restricted administrator approval and execution

## Implementation

- `backend/restricted_onboarding.py`: durable proposal, second-administrator approval, and verified execution.
- `backend/restricted_onboarding_cli.py`: manual workstation-only command with hidden access-token prompts. **No GitHub Actions or public HTTP execution endpoint.**
- `backend/tests/test_restricted_onboarding.py`: signed-token tests for independent approval, expiry, tampering, identity binding, recovery and CLI lockout.
- Azure storage: existing `VideoJobs` Table in `sttextvideo1794671698`; no new resource required by the code. Approval records use `PartitionKey=first-owner-approval-v1`; enrollment audit and project/user rows use existing separate partitions.

## Trust boundaries

1. **Propose**: an allowlisted Entra administrator with a *signed* delegated API access token proposes the enrollment of a separate signed-in target. The request binds target subject SHA-256 digest, enrollment ID, deterministic project ID, project name, role `owner`, proposer, and 24-hour expiry.
2. **Approve**: a *different* allowlisted Entra administrator signs in and approves the immutable request. Neither proposer nor target may approve. The approval is create-only and persisted in Azure Table Storage, with matching target, project, role, and expiry.
3. **Execute**: an allowlisted Entra administrator supplies their own fresh signed API token and the target's fresh API token. The service checks proposal and approval identities, expiry, project and target binding **before** any project/user write. It calls `onboard_first_owner`, preserving create-only writes and an audit trail. Conflicting or revoked identity mappings are not overwritten.

The administrative allowlist is a *local, privileged, independently maintained policy file*, never a user-submitted request body. It identifies admins by `sha256(verified_issuer + "|" + verified_sub)`. Use at least **two different authorized Entra accounts**. This application has not yet established or approved those administrators; do not treat any previously authenticated Project Owner as an administrator automatically.

The identity verification requires the API token to be valid for the configured issuer/audience, signed by Entra, unexpired, tenant-bound, and carry delegated `access_as_user`. This is not a blanket authorization grant: scope is necessary but only allowlisted admins may propose, approve, or execute.

## Privileged policy file schema

This is an **illustrative schema, not a ready-to-use policy**. A trusted administrator must securely verify the two distinct signed Entra admin identities and independently approve the file before using it. Store outside the repository, chmod 600, never commit. A user-supplied email or guessed subject hash is not sufficient.

```json
{
  "tenantId": "773e9051-c546-4b75-9432-90f4f8242660",
  "apiClientId": "a5a09b74-aead-455f-9de7-9b844c8a8465",
  "storageAccount": "sttextvideo1794671698",
  "tableName": "VideoJobs",
  "adminDigests": ["<sha256 of verified admin A>", "<sha256 of verified admin B>"]
}
```

## Execution plan (manual, after authorization)

1. An authorized Azure administrator gives an **operator workstation identity** `Storage Table Data Contributor` scoped to the `VideoJobs` table (and confirms no broader powers). Do **not** grant data-write access to `stage-b-audit` or the frontend SPA.
2. Independently establish admin A and admin B and create the privately stored policy with their *verified* identity digests; require approval of the allowlist policy itself. Ensure local Python requirements and `az login` or another approved `DefaultAzureCredential` source are available. API tokens must be obtained securely; there is **no automated token-extraction mechanism** in this implementation.
3. Verify the actual Entra access-token `aud` matches `PROJECT_OIDC_AUDIENCE` (configured in the CLI as `api://a5a09b74-aead-455f-9de7-9b844c8a8465`). If actual `aud` is the app GUID instead, **do not weaken validation or bypass the check**; separately review and correct the trusted configuration before operation.
4. Generate and independently record a canonical enrollment UUID. Use the **same** ID in every stage. Command-line arguments must not contain tokens:
   - `cd backend && PYTHONPATH=. python -m restricted_onboarding_cli propose --admin-policy /secure/path/admin-policy.json --enrollment-id <uuid> --project-name "B1 Test Project" --execute-writes`
   - `cd backend && PYTHONPATH=. python -m restricted_onboarding_cli approve --admin-policy /secure/path/admin-policy.json --enrollment-id <same-uuid> --execute-writes`
   - `cd backend && PYTHONPATH=. python -m restricted_onboarding_cli execute --admin-policy /secure/path/admin-policy.json --enrollment-id <same-uuid> --execute-writes`
5. The CLI prompts for admin/target API tokens in **hidden input** and does not print them. **Do not place JWTs into GitHub Actions inputs, logs, environment variables, argv, public terminals, tickets or this chat.**
6. Inspect both durable proposal and approval, started/completed audits, project, owner membership and active user mapping. A partial error requires privileged operator review before repeating an *identical* enrollment. Never issue a new approval ID to bypass a conflict.
7. Only after records are verified, conduct an isolated non-production, authenticated `/projects` authorization test. Do not turn on project routes or FAL as part of the enrollment command.

## Blocking operational prerequisites

- No independent allowlist of two authorized Entra administrators has been established.
- No dedicated write-capable operator identity has been provisioned or verified for this procedure.
- This tooling does not currently integrate a secure identity-token handoff from the SPA to the administrator's workstation. The test-user token must be handled only through an approved secure channel; do not ask the user to paste it in chat.
- Azure cross-partition writes are not atomic, and a failed completion audit can leave an active mapping pending reconciliation. A restricted operator must inspect and rerun or remediate that partial state.
- Neither PR is merged. GitHub audit identity remains Reader, worker remains Manual, `ENABLE_PROJECT_ROUTES=false`, and paid FAL disabled.

**Status:** Restricted approval/execution implementation ready for security review and CI; live enrollment is deliberately blocked pending the operator prerequisites.

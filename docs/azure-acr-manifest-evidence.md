# GitHub Actions ACR manifest evidence gate

This extension to the existing protected-main **Azure Read-Only Operations** workflow runs after the established ARM/OIDC audit. Its only new live Azure operation is the read-only manifest metadata lookup for the **existing** `textvideo-worker:692bb1b7f7bd467f1d64e7288115424d21506c12` tag:

```text
az acr manifest show-metadata --registry acrtextvideo1794671698 --name textvideo-worker:692bb1b7f7bd467f1d64e7288115424d21506c12 --output json
```

The exact command runs on the existing `azure-readonly-audit` GitHub Environment and existing Reader OIDC identity only, main-only, behind `AZURE_AUDIT_OPERATIONS_ENABLED=true`. It does **not** grant ACR repository data-plane permissions, create identities, request ACR passwords, push images, deploy resources or trigger jobs. PR event only runs mocks.

## Evidence

The workflow uploads `azure-acr-manifest-evidence.json` and `azure-acr-manifest-evidence.md` within the normal 14-day `azure-readonly-operations` artifact. Reports include a canonical `sha256` digest and a *candidate* immutable worker reference only on a successful authenticated metadata lookup with a valid digest. They never include access tokens or raw CLI stderr.

If ACR access is denied, the CLI command is unsupported, or the tag cannot be resolved, the status is `blocked-unverified`, digest is null and no fallback to a Git SHA tag occurs. The job can still complete successfully because failure to obtain optional manifest evidence must not interrupt existing ARM safety reporting. **Do not treat a green workflow as proof of digest verification.** Review the separate evidence state.

A `verified-metadata` state confirms only the digest returned for that tag by ACR metadata under the Reader identity at the time checked. It does not verify independent provenance, signing, vulnerability posture, container pull authorization, retention or a future deploy. A tag can move; re-resolve immediately before any separately approved deployment.

## Relation to PR #37

PR #37 implements digest-reference validation, Bicep constraints, what-if guards, tests and a rollback runbook. Keep PR #37 draft/unmerged until ACR metadata evidence is obtained and reviewed. Do not insert a guessed hash into code. If `blocked-unverified` remains, record the exact permissions gap as an owner decision; do not grant write or registry admin roles as a workaround.

Existing `github-dev-preview` Reader OIDC credential retirement, PR #24/#28 security-sensitive approval, Azure write RBAC, deployments and paid FAL controls remain unchanged.

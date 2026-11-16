# B1 — First user onboarding: operator-controlled runbook

## What was implemented

`backend/first_user_onboarding.py` provides `onboard_first_owner`, an **opt-in library function**, not an HTTP endpoint or scheduled service.

It cryptographically validates a genuine API access token with `verify_entra_access_claims`, enforcing the configured Entra single-tenant issuer, signed `tid`, correct `aud`, signature, expiration and exact delegated `access_as_user` scope. The token is **never persisted or logged**.

Before any write, it requires:
- A canonical internal `operator_id`, independent `enrollment_id` and approved `project_id`, and normalized project name.
- A trusted `authorize_operator` callback which must verify the **actual** operator's authenticated administrator status, a durable independent approval bound to this enrollment ID, verified subject digest, project ID and explicit `owner` role. A caller-supplied operator ID or callback returning True without independent verification is **not sufficient** for live execution.
- A Table client with explicit `Storage Table Data Contributor` access only to `VideoJobs`, **not** the GitHub `stage-b-audit` Reader identity.

The project ID is deterministically derived from enrollment UUID using `project_id_for_enrollment`; the internal user UUID is deterministically derived from a hash of signed issuer+subject. No user email or UI-provided role establishes identity.

## Azure Table records (VideoJobs)

1. `project-onboarding-audit/first-owner:<enrollment-id>` — start/audit record, written **before** project changes.
2. `projects/<project-id>` — active project and trusted owner ID.
3. `project-members:<project-id>/member:<user-id>` — explicit `owner` membership.
4. `project-oidc-users/<sha256(issuer|sub)>` — active user mapping, **last authorizing write**.
5. `project-onboarding-audit/first-owner-completed:<enrollment-id>` — completion audit record.

All writes are **create-only**: an identical retry reuses existing rows; conflicting owner, revoked mapping, changed operator or enrollment is refused. Azure Table transactions cannot atomically span these partitions. If a write fails, an operator must rerun the *same* independently approved enrollment with a fresh valid token and inspect any orphan rows. An interrupted enrollment is not automatically retried by any service.

The mapping is written after the project and membership records. A failure after the mapping but before the completion audit can leave an otherwise authorized user with an incomplete completion marker; an operator must retry before considering onboarding audited as complete. Monitoring of pending audit records remains a deployment gate.

## Operator steps: NOT YET EXECUTED

1. Confirm the SPA has consent and a user can acquire a genuine API token (already reported by operator).
2. In a restricted operator-only workstation or dedicated workload, verify tenant, signature, audience, expiry and `scp` server-side. Do not email tokens, enter them into GitHub Actions inputs, pass them on a command line, paste them into chat, or print them in logs.
3. Arrange independent, documented approval for creating a development project with the user as Project Owner; bind the approved enrollment UUID and specific project name.
4. Configure the audited operator resolver against **independently stored approval and operator authentication records**. Do not substitute `lambda **kw: True` outside tests.
5. Execute `onboard_first_owner` with a dedicated write-capable Azure Table identity and no FAL credentials, under a controlled operator session. Keep `ENABLE_PROJECT_ROUTES=false` during preflight.
6. Review both audit records, project, owner membership, and active mapping; verify they match the approved enrollment and subject digest.
7. Perform a controlled backend authorization test with the same **genuine** token and check unrelated project denial and revoked membership denial. The API's private network ingress and CORS must be reviewed before browser-to-API testing.
8. Only after separate release approval consider switching `ENABLE_PROJECT_ROUTES=true` in the appropriate non-production environment. Never change paid FAL flags as part of onboarding.

## Explicit security limitations

- The service has **no administrator identity provider** and **no independently persisted approval registry** wired to a production operator. That integration is a blocking deployment prerequisite. The callback design prevents self-service onboarding but depends on correctly secured operator code.
- These are Python unit/integration tests using locally signed RSA tokens and mock Azure Tables; a genuine Entra token has **not** been handled by this onboarding tool yet.
- This operation does not write to Azure, grant permissions, create Entra registrations, or deploy Container Apps by itself.
- Both existing PRs remain draft. No paid rendering or FAL provider call is involved.

# Stage B1 — Frontend Entra registration and first test-user onboarding

## Verified starting state (2026-11-15)

- API registration **Video Render Tool API (dev)** exists in single-tenant Entra ID:
  - Tenant ID `773e9051-c546-4b75-9432-90f4f8242660`
  - API application (client) ID `a5a09b74-aead-455f-9de7-9b844c8a8465`
  - Application ID URI `api://a5a09b74-aead-455f-9de7-9b844c8a8465`
  - Delegated API permission `access_as_user`, admin-consent only, enabled
- `frontend/` is a Vite/React starter, with no verified deployed frontend URL or auth client ID.
- Current infrastructure declares an internal-only Container App API; do **not** treat the API's URL as the frontend redirect URI.
- `ENABLE_PROJECT_ROUTES=false` and `ENABLE_PAID_FAL_WORKER=false` remain safety defaults.
- GitHub audit managed identity is Reader only; no Azure app registration or Table writes are performed by it.

## A. Establish the actual frontend redirect URI before creating the SPA registration

The Entra registration must use a specific intended origin and callback path.
For a local developer-only trial, Vite conventionally serves `http://localhost:5173`, but verify the actual running port first. If chosen, register `http://localhost:5173` as the SPA redirect URI. This localhost option is **not** a deployed production frontend URL.
For a deployed frontend, use the real HTTPS origin and callback route; do not guess an Azure hostname.

## B. Human Entra administrator: register the frontend SPA

1. Entra admin center -> App registrations -> All applications. Find an existing `Video Render Tool Frontend (dev)` before creating a duplicate.
2. If missing, **New registration** with `Video Render Tool Frontend (dev)` and `Accounts in this organizational directory only`.
3. Under Authentication, add the **Single-page application** platform with the verified redirect URI from step A. Do not configure it as Web/confidential client. Do not add an implicit-flow token option or a client secret.
4. Under API permissions -> Add a permission -> **My APIs** -> `Video Render Tool API (dev)` -> **Delegated permissions** -> `access_as_user`.
5. Have an authorized Entra administrator grant organizational consent for this delegated scope, if approved.
6. Record only the non-secret SPA Application (client) ID and the verified redirect URI in frontend configuration.
7. Configure MSAL Browser Authorization Code + PKCE and request `api://a5a09b74-aead-455f-9de7-9b844c8a8465/access_as_user` when calling the API.
8. Validate that access tokens obtained for the API have a signed `iss`, `tid`, `aud`, `scp` and `sub` that the existing backend verifier accepts. Confirm the **actual** `aud` before deploying `PROJECT_OIDC_AUDIENCE`. Never print or paste raw access tokens.

## C. First test-user onboarding (requires real sign-in and a trusted admin write)

The mapping must be based on a **verified token** from the registered Entra user, not a guessed email, arbitrary user ID, or unauthenticated string.

1. An approved test user signs in with the SPA; verify that the access token belongs to the intended tenant and API.
2. In an authorized admin-only operation, extract the **verified** token issuer and subject, and create a new canonical internal user UUID.
3. Compute the existing backend mapping key `sha256(issuer + "|" + subject).hexdigest()` and create the entity in Azure Table `VideoJobs`:
   - `PartitionKey: project-oidc-users`
   - `RowKey: <SHA-256 mapping key>`
   - `userId: <canonical internal user UUID>`
   - `active: true`
4. Create an active project and a membership row using the trusted backend project primitives:
   - project row `PartitionKey: projects`; membership row `PartitionKey: project-members:<projectUUID>`, `RowKey: member:<userUUID>`
   - For the first test user use `owner` **only after explicit project ownership approval**; never derive permissions from an untrusted token role or request body.
5. Use an operator-controlled identity with `Storage Table Data Contributor` on `VideoJobs` for the onboarding transaction; the GitHub Reader audit identity must **not** gain data-plane write access.
6. Verify successful allowed project list, denial for unrelated project, denial after membership revocation, inactive user 401, missing delegated scope 403 and invalid token 401.
7. Keep existing legacy `/jobs` endpoints and paid rendering disabled pending separate release controls.

## D. Explicit blockers / acceptance checks

- [ ] Actual frontend URL / redirect URI confirmed
- [ ] Frontend SPA app registered; client ID recorded
- [ ] Admin consent for delegated `access_as_user` granted
- [ ] SPA MSAL sign-in implemented and tested
- [ ] Verified test user signs in; server validates real token
- [ ] Admin-only identity mapping, project and membership records created
- [ ] Cross-project and revoked-user tests pass in a controlled dev environment
- [ ] Authenticated API can be reached from intended frontend origin (CORS/ingress/security review)
- [ ] Operator separately authorizes enabling project routes
- [ ] Paid FAL worker remains disabled

Do not use the GitHub OIDC audit identity as a user identity or a write-capable onboarding principal.

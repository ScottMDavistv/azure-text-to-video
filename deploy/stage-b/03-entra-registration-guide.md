# B1 — Entra user authentication app registration (administrator handoff)

## Existing state
- GitHub audit OIDC identity is for **automation**, NOT end-user login.
- `id-textvideo-api-dev` is an Azure managed identity for workloads, NOT a human-login app registration.
- The audit principal has resource-group Reader, and the first `az ad app list` calls returned **Insufficient privileges**. Consequently, no application registration can be confirmed or created using that identity alone.

## Register or identify application — human Entra administrator

1. In the Microsoft Entra admin center, open **Identity > Applications > App registrations** and find an existing app specifically for `Video Render Tool API`. Avoid creating a duplicate if one already exists.
2. If missing, **New registration**: display name `Video Render Tool API (dev)`; supported account type **Accounts in this organizational directory only**. Record the **Application (client) ID** and **Directory (tenant) ID**. Do not create a client secret.
3. Under **Expose an API**, configure an application ID URI (recommended `api://<application-client-id>`) and an enabled delegated scope `access_as_user`. Set consent metadata with admin review. Do not grant privileged Graph application permissions.
4. Register the intended frontend separately as a public SPA only when the frontend origin / redirect URI is known. Use Authorization Code + PKCE. Do not invent redirect URIs or grant implicit-flow tokens.
5. Store nonsecret settings in Azure app configuration after a separate deployment approval. Keep `ENABLE_PROJECT_ROUTES=false` until signed tokens, user mapping, and project authorization integration tests pass.
6. Provision internal user mappings from `<issuer>|<sub>` to canonical internal user UUIDs using a restricted trusted admin workflow. No self-registration of administrator roles. Review tenant and issuer constraints.
7. Configure the API access token validation environment:
   - `PROJECT_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0`
   - `PROJECT_OIDC_JWKS_URL=https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys`
   - `PROJECT_OIDC_AUDIENCE=api://<api-application-client-id>` **only if the issued `aud` actually matches**; some Entra tokens use the client GUID instead.
8. Test expected audience/issuer with a genuine signed **test** access token, not an ID token. Never paste the token into GitHub issues, logs, or this conversation.

## Required release tests
- Invalid signature, wrong audience, wrong tenant, expired token -> 401
- Unmapped/inactive subject -> 401
- Cross-project access, revoked membership and unauthorized approval -> 403
- Valid mapped member -> only authorized project endpoints
- No secret stored in code, GitHub outputs, or workflow parameters

### No changes executed in Azure
Entra app creation/consent and provisioned user mapping require authorized directory privileges and a validated frontend configuration. The GitHub audit identity intentionally has neither.

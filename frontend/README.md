# Video Render Tool — local Microsoft Entra sign-in (B1)

This frontend is a local development-only React/Vite SPA. It requests a delegated
Microsoft Entra API access token using MSAL Browser and Authorization Code + PKCE.
**It does not render paid videos, write user mappings, or assign project roles.**

## 1. Create the frontend SPA application in Microsoft Entra

Use an Entra administrator account in the same tenant as the existing API.

1. Entra admin center → App registrations → All applications: search for
   **Video Render Tool Frontend (dev)** and reuse if already created.
2. Otherwise select **New registration**.
   - Name: `Video Render Tool Frontend (dev)`
   - Supported accounts: **Single tenant** (Starving Man Design LLC)
   - Redirect URI platform: **Single-page application (SPA)**
   - Redirect URI: **`http://localhost:5173`**
3. Register. Record the **Application (client) ID** for this *frontend*
   registration. It must differ from the API application ID.
4. Under **Authentication**, verify the SPA platform contains
   `http://localhost:5173`. Do not turn on implicit grant or add a client
   secret. Do not authorize the legacy GitHub automation app as a frontend.
5. Under **API permissions → Add a permission → My APIs**, select
   **Video Render Tool API (dev)**, choose **Delegated permissions**, then
   **`access_as_user`**. Authorized tenant administrator grants
   admin consent after review.

The existing API scope is
`api://a5a09b74-aead-455f-9de7-9b844c8a8465/access_as_user`.
Tenant: `773e9051-c546-4b75-9432-90f4f8242660`.

## 2. Start local frontend

From your development environment, with Node.js installed:

```bash
cd frontend
cp .env.example .env.local
```

Edit **frontend/.env.local** and set ONLY the public SPA application ID:

```text
VITE_ENTRA_SPA_CLIENT_ID=<frontend-spa-client-id>
```

Then run:

```bash
npm ci
npm run dev
```

Open exactly **http://localhost:5173**. Vite is configured to refuse a
different port, avoiding silent Entra redirect mismatches.

Select **Sign in with Microsoft**, then **Check API permission**.
The browser does not display, log, or send the raw access token to ChatGPT.
An authenticated account or API token alone does **not** create a project
membership.

If the API permission check fails, verify SPA redirect, administrator consent,
and the app's API permission. Do not solve it by disabling token validation.

## 3. First test-user onboarding

A trusted administrator must confirm a genuine API-scope access token's
signature, tenant, audience, `scp`, and `sub` using a server-side verifier.
Then provision an internal user mapping and explicit project membership
according to `../deploy/stage-b/07-frontend-entra-and-first-user.md`.

The present API is internal-only and project routes are disabled. Do not
enable them merely because local login works. A future administrator-only
onboarding tool is required to create records with audited authorization,
idempotent writes, and manual recovery of partial failures.

**Never paste or commit tokens, raw credentials, user IDs, or keys.**
The GitHub `stage-b-audit` managed identity remains Reader only.

## Safety defaults

- `ENABLE_PROJECT_ROUTES=false` in Azure
- `ENABLE_PAID_FAL_WORKER=false`
- No FAL credentials in frontend or CI
- Do not use the API application ID for `VITE_ENTRA_SPA_CLIENT_ID`
- No Azure deployment or paid FAL job is performed by frontend CI

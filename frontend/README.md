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
3. Register. Verified frontend SPA client ID: `6d5e9e96-8be6-445c-9176-3b67d020612c` (distinct from the API client ID).
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
VITE_ENTRA_SPA_CLIENT_ID=6d5e9e96-8be6-445c-9176-3b67d020612c
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

---

# Mock creative-studio workflow (development only)

The React/Vite frontend now contains an offline studio UI, a responsive session dashboard, deterministic 300-second/five-scene storyboard placeholders, job lifecycle simulator, status indicators, and disabled preview/download affordances.

## Local checks

```sh
cd frontend
npm ci
npm test
npm run lint
npm run build
npm run dev
```

The simulation requires no backend, login, FAL credentials, Azure resources, storage or paid calls. Enter 1–2,000 characters and select **Create mock job**; select **Advance simulation** to move from queued through planning, rendering, assembling and completed. A simulated completion **does not** create a video or downloadable artifact; play and download are intentionally disabled. State exists only in browser memory and resets on reload.

Existing Entra local authentication stays separate from the simulation. The production prompt/status/download integration must wait for Design Management acceptance of the backend contract and worker/artifact path, plus authorization decisions for scoped user access. Never replace security validation with mock job data. Do not put access tokens in logs, page markup, environment variables served to the browser or demo state.

Scope: isolated frontend development PR only. No CI deployment, Azure/IAM/finance changes, Copilot dispatch, automatic merge, or paid rendering.

## Authenticated prompt-to-video-to-download integration (development only)

The opt-in React `Authenticated project` mode requests a Microsoft Entra
`access_as_user` token via MSAL on each API request and never stores/logs that
token. It talks exclusively to `/projects/{project_id}/jobs` scoped backend
routes, **not** legacy `/jobs` service-token endpoints. Each API operation
requires verified Entra signature, issuer, audience, delegated scope, active
mapped user, active project membership, and server-recorded job `createdBy`
and `projectId`. Legacy jobs without ownership metadata cannot be accessed.

Frontend activation (public configuration only):
```text
VITE_ENABLE_AUTHENTICATED_JOURNEY=true
VITE_VIDEO_PROJECT_ID=<authorized project UUID>
VITE_VIDEO_API_ORIGIN=https://<approved API origin>
```
Only use a separately approved same-origin reverse proxy or explicitly
approved HTTPS backend origin. Do not embed `API_ACCESS_TOKEN`,
`VIDEO_DOWNLOAD_TOKEN`, client secrets, FAL credentials, or other privileged
service tokens in a SPA.

Backend flags default OFF and must be independently approved after project
user enrollment, working JWKS validation and secure API routing:
```text
ENABLE_PROJECT_ROUTES=false
ENABLE_AUTHENTICATED_VIDEO_JOBS=false
ENABLE_SYNTHETIC_VIDEO_DISPATCH=false
ENABLE_VIDEO_DOWNLOADS=false
VIDEO_RENDER_PROVIDER=disabled
ENABLE_PAID_FAL_WORKER=false
ENABLE_LIVE_VIDEO_RENDERING=false
```

With authorized flags, the frontend submits a prompt, queues the server-created
project/owner-scoped job, polls its real job status (no invented progress
percentage), retrieves private artifact metadata, and fetches authenticated MP4
bytes to an ephemeral browser Blob URL for preview/download. URLs are revoked
on replacement/unmount. The user must be provisioned as a project member.

**Integration is not production/live accepted:** The current deployed Azure API
and worker are not configured for these new routes; the synthetic plan/FFmpeg/
private Azure Blob lifecycle is not proven end-to-end. Enabling synthetic
dispatch is contingent on separately verified renderer, queue reliability,
artifact storage and safe worker activation; enabling paid rendering is never
part of this change. A failed/disabled queue retains a created job and reports
an error without manufacturing an MP4. The default offline simulator remains
available regardless of backend status.

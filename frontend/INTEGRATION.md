# Authenticated frontend integration — development contract

The frontend offers two separate experiences:

1. **Offline mock workflow**: in-memory demonstration; not a real video.
2. **Authenticated workflow**: direct browser client for the documented existing endpoints. Uses Microsoft Entra delegated API scope, never a service credential. It is not production-ready against the current backend.

The connected adapter implements the current backend sequence:
`POST /jobs` → `POST /jobs/{job_id}/queue` → poll `GET /jobs/{job_id}` → `GET /jobs/{job_id}/video/metadata` → `GET /jobs/{job_id}/video`.

Status is shown as the actual string value; **no invented progress percentage**. On completion, the client checks the job UUID, metadata availability, MIME type, exact expected relative blob endpoint, 100 MiB limit and the returned blob's size before enabling playback and download. Authenticated bytes remain in a local object URL, revoked on navigation/unmount. HTTP errors are summarized safely without reflecting raw server error bodies, secrets or tokens.

## Local configuration (only after backend support)

- Keep `VITE_ENTRA_SPA_CLIENT_ID` as a **public** SPA application ID.
- `VITE_JOB_API_BASE_URL` can be set to the HTTPS **origin** of a development-only job API that supports per-user Entra authentication and project/job ownership; HTTP is accepted only on localhost.
- Never set `VITE_API_ACCESS_TOKEN`, `VITE_VIDEO_DOWNLOAD_TOKEN`, or any other service secret. Vite exposes `VITE_` variables to browser users.
- The API must be reachable from the SPA's origin with an appropriately limited CORS policy; no CORS or public network change is made here.
- If endpoint configuration is missing, submission is disabled. A sign-in account alone is not enough to enable the connection.

## Blocked backend policy decision

The **current** `backend/api_access.py` authenticates `/jobs` with a *shared* `API_ACCESS_TOKEN`; `backend/video_routes.py` independently requires a *shared* `VIDEO_DOWNLOAD_TOKEN` and `ENABLE_VIDEO_DOWNLOADS`. Neither is an Entra delegated access token. The API is internal-only and has no per-user job ownership checks.

**Do not place either shared token in browser code, local storage, Vite environment settings, or a client-side reverse proxy. Do not set `ALLOW_UNAUTHENTICATED_DEV` or `ENABLE_VIDEO_DOWNLOADS` to get around this.** User-level authentication, ownership-scoped job query/queue/download and a network architecture decision are mandatory before real user requests. The frontend cannot unilaterally solve this.

Proposed owner decision (separate security review): accept signed single-tenant Entra access tokens with issuer, audience, scope and subject verification; resolve an internal user/project identity server-side; assign each job a trusted owner on creation, enforce ownership for submission/status/queue/download and no cross-tenant access. Keep the existing service endpoints separate and private, or replace them behind a trusted backend-for-frontend. Enable download only after least-privilege design, tests and explicit rollout approval.

The synthetic worker-to-FFmpeg-to-private-blob lifecycle is not yet independently verified as end-to-end. This branch does not deploy or enable rendering. Run `cd frontend && npm ci && npm test && npm run lint && npm run build` and review CI before any merge. Existing governance forbids autonomous merges, Copilot overages, Azure deployment and paid provider calls.

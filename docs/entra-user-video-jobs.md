# Entra user-owned video API — development security proposal

This PR adds separate `/user/jobs` routes; it does NOT alter private `/jobs` middleware, service tokens, public networking, or Azure configuration.

## Trust boundary
- Feature requires both `ENABLE_USER_VIDEO_JOBS=true` and existing `ENABLE_PROJECT_ROUTES=true`. Both remain **false** unless independently deployed and approved.
- Every user route requires a bearer Entra delegated API token signed by a trusted single-tenant JWKS, with exact configured issuer, audience, tenant, expiry, `sub`, and `access_as_user` scope. It reuses `verify_project_token`; the server resolves a pre-provisioned active internal user mapping.
- A prompt request accepts only a project UUID and 1–2000 character prompt. A *trusted server-side project membership* permits the create, and the server stores `projectId` and `ownerUserId` on the job row. The request cannot choose its owner.
- Each status/queue/download request must match the immutable stored owner ID and have current membership in an active project. Unknown, unowned legacy, and other-user jobs fail closed. Revoked users cannot access previous jobs via user routes. This intentionally disallows sharing jobs among project members until there is a separately reviewed sharing policy.
- Existing service credential middleware still protects `/jobs`, independently from these new routes. The shared `VIDEO_DOWNLOAD_TOKEN` guard and existing `/jobs/{id}/video` endpoints remain unchanged.
- New user metadata/stream endpoints reuse private blob resolution and enforce both feature flags and `ENABLE_VIDEO_DOWNLOADS`. No download flag is turned on. Artifact sizes remain bounded to 100 MiB by the private blob resolver. No signed public blob URLs.
- Queue uses existing ETag transition and dispatch recovery implementation; no worker/provisioning/finance change.

## User API contract
`POST /user/jobs`: `{"project_id":"<canonical UUID>","prompt":"..."}` → `201 {"job_id":"...","status":"created","created_at":"..."}`

`POST /user/jobs/{job_id}/queue` → queued result; `GET /user/jobs/{job_id}` → job status; `GET /user/jobs/{job_id}/video/metadata` → private metadata with user-scoped relative download path; `GET /user/jobs/{job_id}/video` → private MP4.

Current frontend draft PR #40 targets `/jobs` and needs **separate development-only adaptation** for `/user/jobs` and project selection after this backend contract passes security review. The SPA's public origin, CORS, and secure network access must be approved independently. Do not expose the internal API or embed service tokens in a browser.

## Negative tests
`cd backend && pytest -q tests/test_user_video_routes.py tests/test_project_api_security.py`

Tests cover disabled flags, missing/invalid bearer, missing scope, ownership injection, inactive/revoked project membership, cross-user and legacy job denial, inaccessible blob, private metadata, queue mutation blocking, and legacy service middleware preservation.

## Release conditions (not authorized by this PR)
1. Exact-head backend/security CI and independent single-owner supervised security review.
2. Confirm the live Entra access token audience matches configured `PROJECT_OIDC_AUDIENCE`; do not paste JWTs into tickets/logs.
3. Confirm user provisioning and project membership through audited onboarding, and active-project checks against real Table data.
4. Threat model duplicate/queued job transitions, blob authorization races, CORS allowlist, streaming limits, and session revocation. Validate with adversarial integration tests.
5. Separate authorization for Azure IAM, networking, feature-flag activation, user download enablement, deployment and paid providers. All remain unchanged now.

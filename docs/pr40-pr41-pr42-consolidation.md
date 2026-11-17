# Authenticated PR consolidation: #40 + #41 → #42

**Selected canonical contract:** `/projects/{project_id}/jobs` with the verified project ID in the route, plus `/{job_id}`, `/{job_id}/queue`, `/{job_id}/video/metadata` and `/{job_id}/video`. Legacy `/jobs` service bearer endpoints remain separate, unchanged, and **must never be used by the browser**.

## Feature-preservation matrix

| Source | Requirement | Consolidated #42 implementation |
|---|---|---|
| #40 | Frontend signed Microsoft Entra API token; never Vite service credentials | `SecureStudio.tsx` + `secureJourney.mjs`, per-request `acquireApiToken()` |
| #40 | Explicit HTTPS origin, local-only HTTP exception, no redirect forwarding | `createVideoApi`, `redirect:'error'`, `credentials:'omit'`; no service token |
| #40 | Prompt submission, queue, real status polling, recoverable errors | `SecureStudio.tsx`, server-provided job status and explicit queue retry |
| #40 | Strict UUID, job state, private MP4 MIME, size and exact download URL | `secureJourney.mjs`; now verifies metadata state, integer size, `Content-Length` and Blob size equality |
| #40 | Ephemeral browser video Blob URL lifecycle | `SecureStudio.tsx`, revoke on URL replacement or unmount |
| #41 | Signed Entra JWKS, issuer/audience/tenant/scope and active mapped user | Shared `verify_project_token`, server-side `verified_actor`; no separate trust implementation |
| #41 | Active project membership and revocation checked on every job access | `authorized_job` and `get_context` call `require_project_role` on request; never rely on browser claims |
| #41 | Immutable server-stamped job owner/project | `create_project_job` writes `createdBy`, `projectId`; browser cannot supply either |
| #41 | Legacy/unowned and cross-user/project denied | `authorized_job` rejects missing/mismatched fields, returns 404 |
| #41 | Private blob never read for uncompleted job | `authorized_video` now explicitly requires `status='completed'` after ownership check |
| #41 | Private no-cache, no-sniff bounded MP4 delivery | `project_video_download` uses existing `get_video_resource` size guard and streaming cap |
| #41 | Disabled-by-default user API and downloads | `ENABLE_AUTHENTICATED_VIDEO_JOBS`, `ENABLE_PROJECT_ROUTES`, `ENABLE_VIDEO_DOWNLOADS`, `ENABLE_SYNTHETIC_VIDEO_DISPATCH` all default off |
| #42 | Provider-free 300s synthetic FFmpeg and bounded worker seam | `synthetic_job_renderer.py`, `synthetic_worker_cli.py`, offline real MP4 acceptance tests |
| #46 | MSAL token handling improvements, OIDC/financial/security hardening | **Partial overlap only:** #42 retains redirect-based login/logout and token acquisition; current PR #46 uses popup flows. These files are NOT identical. An independent conflict/behavior review is required before merging PR #42. |

## Deliberate exclusions and unresolved security gates

- **Do not register parallel `/user/jobs` endpoints from #41** or adopt #40's direct `/jobs` service-token path; both are superseded by the project-scoped contract. Documented #41 tests are adapted to the project-scoped boundary instead of blindly ported.
- #41's additional test for revoked membership and strict canonical project IDs must be retained through negative-path regression coverage in #42. Existing signed-token helper is the sole Entra verification boundary.
- #40's fetch network error handling is only partially represented; any untrusted server response must remain generic and must never echo tokens or internal details.
- No production CORS/ingress/Entra registration, project enrollment, worker launch, Azure IAM/deployment, paid FAL or automatic dispatch authorized.
- The default Azure worker is still inspection-only. Full live Azure prompt → Queue → owned synthetic worker → private blob → authenticated browser MP4 has **not been validated**.
- PR #46 has merged to main. PR #42 still contains its older redirect-based auth implementation; reconcile against main's popup-based entraAuthCore before integration, and independently verify account-switch behavior.

**Decision:** Keep #40 and #41 open as draft records until this canonical #42 branch is independently reviewed and explicitly approved for merge. Do not close or merge any PR in this consolidation task.

## Direct PR #46 overlap check

Source PR #46 changes `frontend/src/entraAuth.ts`, while #42 also needs it for authenticated browser requests. The project API route code in #42 does **not** replace or weaken #46's finance/admin auth, provider receipt identity or Copilot author verification changes; those other modules are not copied or activated. The shared file is NOT identical: #42 uses redirect flows and #46 uses popup flows. Do not assume compatibility. Select a single reviewed user interaction model and rerun authentication/session tests and full CI before any integration. PR #46 has now merged; its security-reviewed authentication core and other controls are the mandatory baseline for PR #42 integration.

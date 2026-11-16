# Video job API contract

This document records the existing job API for non-billable integration and
identifies fields that are not yet implemented. It does not enable rendering,
downloads, or any provider. A synthetic 300-second render is not currently
connected to this API.

## Authentication

Every `/jobs` request requires the configured API access token in an HTTP
`Authorization` header using the `Bearer` scheme.

The configured service token must be at least 32 characters. Missing or invalid
configuration fails closed (`401` for a missing/invalid request token; `503`
when the server token is not configured). `ALLOW_UNAUTHENTICATED_DEV=true` is
for explicit local development only and must not be used in deployed
environments.

This is service-level authentication, not per-user identity or job ownership.
Video downloads have a second, independent service-token check and are disabled
unless `ENABLE_VIDEO_DOWNLOADS` and `VIDEO_DOWNLOAD_TOKEN` are configured.
Neither mechanism authorizes public or user-specific access.

## Submit and poll

Submit a prompt with `POST /jobs`. Prompts must contain 1–2,000 characters.

```http
POST /jobs
Content-Type: application/json
```

```json
{
  "prompt": "A calm ocean at sunrise"
}
```

The existing API returns `201 Created`:

```json
{
  "job_id": "71d0d32a-8532-42e5-b710-3e8b71a3126c",
  "status": "created",
  "created_at": "2026-11-16T12:00:00+00:00"
}
```

Poll `GET /jobs/{job_id}` for the current state:

```json
{
  "job_id": "71d0d32a-8532-42e5-b710-3e8b71a3126c",
  "prompt": "A calm ocean at sunrise",
  "status": "processing",
  "created_at": "2026-11-16T12:00:00+00:00"
}
```

The current state transitions are `created` → `queued` → `processing` →
`completed` or `failed`. `POST /jobs/{job_id}/queue` starts queue dispatch;
dispatch conflicts return `409` and an uncertain dispatch result returns
`503`. Polling currently exposes status only: there is no percentage, estimate,
or other progress field. Frontends should not infer percent complete.

## Completion and download metadata

For a completed job, `GET /jobs/{job_id}/video/metadata` returns metadata when
downloads are enabled and both service-token checks pass:

```json
{
  "job_id": "71d0d32a-8532-42e5-b710-3e8b71a3126c",
  "status": "completed",
  "available": true,
  "content_type": "video/mp4",
  "size_bytes": 12582912,
  "download_endpoint": "/jobs/71d0d32a-8532-42e5-b710-3e8b71a3126c/video"
}
```

The download endpoint streams a private MP4; metadata and download requests
return `503` while downloads are disabled. An unfinished job returns `409`, a
missing job or artifact returns `404`, and an artifact over the 100 MiB limit
returns `413`. The download route does not create a signed public URL.

## Failures and compatibility

HTTP errors use FastAPI's existing `{"detail": ...}` response shape. For
example, an unknown job returns:

```json
{
  "detail": "Video job not found"
}
```

Invalid prompt input returns `422` with FastAPI validation details; missing or
invalid authentication returns `401`; an illegal state transition returns
`409`. These responses and the existing job response fields are the current
compatibility contract.

A job in the `failed` state is currently returned by `GET /jobs/{job_id}` with
the same fields as other states and `"status": "failed"`. Although the worker
records an internal failure type, the GET response does not expose a structured
job-level error. Neither a structured `error` object nor percentage progress is
implemented. Additive failure and progress fields need agreement with the
worker/frontend before implementation; this document does not imply they are
available.

Current API example for a failed job:

```json
{
  "job_id": "71d0d32a-8532-42e5-b710-3e8b71a3126c",
  "prompt": "A calm ocean at sunrise",
  "status": "failed",
  "created_at": "2026-11-16T12:00:00+00:00"
}
```

For frontend/worker agreement, a future additive failure payload could expose
the recorded failure as:

```json
{
  "job_id": "71d0d32a-8532-42e5-b710-3e8b71a3126c",
  "prompt": "A calm ocean at sunrise",
  "status": "failed",
  "created_at": "2026-11-16T12:00:00+00:00",
  "error": {
    "code": "render_failed",
    "message": "Video rendering failed",
    "retryable": false
  }
}
```

This is a proposal only; current API responses do not include `error`. The
frontend and worker owners must agree on field names, codes, and retry semantics
before it is implemented.

## Non-billable and integration limits

The API contract does not itself run a renderer. The synthetic 300-second
fixture is an offline validation tool, not proof of end-to-end rendering or
download. The frontend and worker still need to agree on progress and
job-level failure details, and the synthetic worker/artifact lifecycle must be
integrated separately. Paid providers remain disabled by default; do not enable
them to validate this contract.

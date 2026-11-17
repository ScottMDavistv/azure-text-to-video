# Backend job administration

## Administrative endpoints

- GET `/admin/jobs`
- GET `/admin/jobs/{job_id}`
- POST `/admin/jobs/{job_id}/cancel`

These endpoints are disabled by default.

## Enabling administration

The administrative feature gate is:

`ENABLE_JOB_ADMIN_ENDPOINTS=true`

A separate environment variable is required:

`JOB_ADMIN_TOKEN`

The token must contain at least 32 characters and must be an ASCII
RFC 7235 `token68` value (letters, digits, `-._~+/`, optional trailing
`=`), for example the output of `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
A token outside that grammar (non-ASCII, embedded whitespace or other
punctuation) cannot be sent reliably in an HTTP `Authorization` header,
so the endpoints fail closed with `503 Job administration is not
configured`. The configured value is never echoed in responses or in
the runtime security status report.

Do not commit real tokens or put them in URLs.

Use Azure Container Apps secrets for any deployed token.

These are service-level administrative credentials, not user
authentication or job ownership authorization.

Do not expose administrative routes publicly.

## Listing

The list endpoint supports:

- `status`: optional status filter
- `limit`: 1–50 jobs, default 20
- `cursor`: optional job UUID cursor

The cursor is RowKey-based.

Results are NOT sorted by creation time because RowKeys use UUIDs.

Pagination is not a historical snapshot. Concurrent job changes
can affect subsequent pages.

The current implementation is suitable for limited administrative
inspection, not large-scale analytics or a customer gallery.

## Cancellation

Only `created` and `queued` jobs can be cancelled.

Cancellation uses the job's Azure Table Storage ETag to prevent
overwriting a worker's concurrent update.

Cancellation does not delete an existing queue message.

A cancelled queued job may still be received by the worker, but
its normal queued-only claim should prevent rendering.

Cancellation of `processing`, `completed`, and `failed` jobs is
rejected.

There is no external provider cancellation yet.

## Existing API security

The original `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/queue`,
and `/jobs/{job_id}/retry-dispatch` endpoints are not yet protected
by user authentication.

The video download endpoints have separate interim service-level
token protection.

Keep the Azure backend internal-only until the full authentication
and authorization phase is completed.

## Deployment

This batch changes repository code only.

It does not deploy to Azure, create secrets, change ingress,
start workers, or enable external model generation.

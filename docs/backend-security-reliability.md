# Backend security and reliability foundations

## Existing API authentication

`REQUIRE_JOB_API_AUTH=true`

When enabled, all paths under `/jobs` require:

`Authorization: Bearer <API_ACCESS_TOKEN>`

`API_ACCESS_TOKEN` must be at least 32 characters.

Do not commit actual tokens.

Configure deployed credentials using Azure secrets.

The API access token does not establish a user's identity
or ownership of a video job.

Administrative endpoints use a separate feature flag
and token. Video delivery also has its own token.

Do not expose the API publicly until user-level ownership,
authorization, abuse protection and operational testing
are completed.

## Worker retries

`worker_retry.py` provides bounded, deterministic
exponential-backoff calculations.

It does NOT automatically retry video submissions.

Avoid duplicate billable submissions when a provider request
succeeds but its HTTP response is lost.

Retry policy integration requires provider request IDs and
durable state to prevent duplicate charges.

## Rendering budgets

`render_budget.py` validates operator-provided monetary
estimates and budget thresholds.

These are advisory preflight calculations, not authoritative
financial controls.

The following configuration fields can be inspected:

- VIDEO_PER_JOB_LIMIT_USD
- VIDEO_MONTHLY_LIMIT_USD
- VIDEO_ESTIMATED_JOB_COST_USD
- VIDEO_RECORDED_SPEND_USD

No pricing is assumed by the code.

These checks are not yet wired to live generation.

The recorded-spend amount is not automatically reconciled
to provider billing.

Do not describe these controls as a guaranteed $50 cap.

## Operational events

`operational_events.py` supports structured JSON events
without including video prompts, credentials, video bytes
or arbitrary exception messages.

Administrative cancellation emits a structured event.

Centralized telemetry ingestion remains future work.

## Diagnostics

Run locally:

`PYTHONPATH=backend python backend/operations_cli.py --check`

The result contains safe configuration flags only.

It does not establish live Azure connectivity or health.

## Deployment limitations

This batch makes code and tests changes only.

No Azure deployments or live paid rendering occur.

The actual queue-consuming worker has not been deployed.

The existing `/jobs` API remains unauthenticated by default
for compatibility with the current internal-only deployment.

Production ingress must remain private until the
authentication and authorization work is completed.

# Video Render Worker — Deployment Runbook

## Current status

The repository contains a dedicated worker container.

The worker is not deployed or automatically started.

The default container command is:

`python worker_runtime.py --check`

This prints configuration readiness without connecting to
Azure or requesting a billable video generation.

## Worker image

Build from the repository root:

`docker build -f backend/Dockerfile.worker -t textvideo-worker:dev backend`

Inspect configuration:

`docker run --rm textvideo-worker:dev`

Do not start a live worker until a provider and billing
controls have been reviewed.

## Future Azure deployment

The worker should be deployed separately from the API
container, under the existing private infrastructure.

Possible hosting approaches include:

- Azure Container Apps worker deployment
- Azure Container Apps event-driven Job

The final choice requires checking workload duration,
queue semantics, scaling, timeouts, and expected billing.

Do not run the full existing infrastructure deployment
workflow without inspecting its what-if changes.

Use an isolated infrastructure template for worker changes.

## Identity and permissions

The worker will need least-privilege access to:

- Azure Queue Storage: textvideo-jobs
- Azure Table Storage: VideoJobs
- Azure Blob Storage: videos

Prefer a dedicated worker managed identity.

Do not assign storage account keys or grant broad
subscription-level access.

## Live worker safety gates

The following are required before `--run` is permitted:

- ENABLE_WORKER_CONSUMPTION=true
- ENABLE_LIVE_VIDEO_RENDERING=true
- ACKNOWLEDGE_PROVIDER_BILLING_RISK=true
- VIDEO_RENDER_PROVIDER=fal
- FAL_MODEL_ID
- FAL_KEY
- AZURE_STORAGE_ACCOUNT
- AZURE_JOBS_QUEUE
- VIDEO_PER_JOB_LIMIT_USD
- VIDEO_MONTHLY_LIMIT_USD
- VIDEO_ESTIMATED_JOB_COST_USD
- VIDEO_RECORDED_SPEND_USD

These flags do not establish real Fal model access.

They do not guarantee spending stays below a monthly
budget.

Do not enable billable rendering without durable job-cost
reservation and provider billing reconciliation.

## Worker execution

A bounded worker run uses:

`python worker_runtime.py --run --max-iterations 1`

A continuous worker uses:

`python worker_runtime.py --run --continuous`

Both are blocked until all readiness checks pass.

## Shutdown and recovery

SIGTERM and SIGINT request graceful shutdown.

A currently executing renderer may continue until it
returns, errors, or times out.

The Azure Queue visibility lease attempts periodic
renewals during processing.

A worker crash may leave the job marked processing.

Existing stale-job detection supports controlled recovery,
but does not automatically rerender failed jobs.

Further work is required for production-grade heartbeat,
job reconciliation, provider submission idempotency,
and crash-safe recovery.

## Security

Do not expose worker HTTP ingress.

The worker container does not serve a public API.

Never embed provider credentials in Docker images.

Use Azure Container Apps secrets or an approved
managed secret store.

Keep the existing API internal-only until user
authentication and job ownership authorization are ready.

## Validation before deployment

1. All unit tests pass.
2. Worker container builds independently.
3. Readiness command completes without network requests.
4. No live rendering is enabled.
5. Azure resource deployment what-if shows only
   intended worker resources.
6. Managed identity roles are reviewed.
7. Azure image repository and tag are verified.
8. Operator explicitly approves activation.

## Deployment is not included in this batch

No Azure resources, secrets, ingress settings,
worker replicas, or model configuration are changed.

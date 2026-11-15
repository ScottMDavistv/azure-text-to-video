# Fal API Integration — Video Render

## Objective

Use a hosted Fal video-generation endpoint from the
existing Azure backend and worker.

Do not deploy a custom GPU model.

## Official documentation

- https://fal.ai/get-started.md
- https://github.com/fal-ai/fal
- https://fal.ai/docs/api-reference/inference-api/queue/submit
- https://fal.ai/docs/api-reference/inference-api/queue/status
- https://fal.ai/docs/api-reference/inference-api/queue/result

## Python SDK

Installed package:

`fal-client`

Imported module:

`fal_client`

The client discovers its credential from `FAL_KEY`.

## Credential setup

Create an API key in the Fal dashboard.

Never commit it to GitHub.

Never put it in Docker build arguments.

Never include it in diagnostic output.

When deploying, configure it as an Azure Container Apps
secret and reference it from the worker environment.

## Model selection

Select a model from Fal's hosted text-to-video catalog.

Example candidate:

`fal-ai/kling-video/v3/pro/text-to-video`

Model access, current input schema, supported durations,
price, and output format must be verified before use.

Configure the endpoint ID using:

`FAL_MODEL_ID`

## Safe validation

Run:

`PYTHONPATH=backend python backend/fal_api_setup.py --check`

This checks configuration and SDK installation.

It does not authenticate against Fal.

It does not submit a generation.

It does not incur Fal inference charges.

## Existing provider implementation

The repository already includes:

- fal_provider.py
- fal_polling.py
- fal_renderer.py
- render_pipeline.py
- video_download.py
- video_delivery.py

These modules implement a REST-based Fal queue integration.

The new SDK is available for future integration but
does not replace the existing queue adapter automatically.

Avoid mixing SDK and REST retries for the same job.

## Queue execution

Recommended future execution order:

1. Validate an authenticated job request.
2. Check durable generation budget.
3. Claim the queued Azure job conditionally.
4. Persist an external submission intent.
5. Submit once to Fal.
6. Persist the returned Fal request ID.
7. Poll or reconcile the request.
8. Retrieve completed output.
9. Download and validate MP4.
10. Upload MP4 into private Azure Blob Storage.
11. Mark the Azure job completed.
12. Acknowledge the Azure Queue message.

A lost response after submission is an unresolved
duplicate-billing risk.

Do not enable automatic billable retries until durable
provider request recovery is implemented.

## Initial operational settings

Keep the following disabled:

ENABLE_LIVE_VIDEO_RENDERING=false

ENABLE_WORKER_CONSUMPTION=false

Keep the Azure API internal-only.

## Deployment status

Installing fal-client into requirements does not
update the existing Azure API or worker images.

A new build, what-if review, and controlled Azure
deployment are needed for cloud adoption.

## First paid test

Requires separate approval.

Choose the selected model, duration, estimated cost,
and maximum test spend before submission.

Do not perform live generation during setup.

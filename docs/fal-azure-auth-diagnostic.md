# Azure-side Fal Authentication Diagnostic

## Purpose

Verify that the Azure-hosted worker can reach Fal's
Queue Status API using its Key Vault-backed credential.

## Command

python worker_runtime.py --verify-fal

## Behavior

- Uses FAL_KEY from the environment
- Uses FAL_MODEL_ID for owner/app routing
- Makes one authenticated HTTPS GET
- Uses a randomly generated nonexistent request UUID
- Never submits a model request
- Never starts worker polling
- Never downloads output
- Never prints secret values or HTTP response bodies

## Interpretation

404:
  Fal queue endpoint reachable; authentication
  and model entitlement remain inconclusive.

401:
  Fal rejected the supplied credential.

403:
  Access denied. May indicate account permissions.

429:
  Rate limited.

5xx:
  Fal server-side error.

Network failure:
  Could not complete the read-only request.

A 404 MUST NOT be reported as confirmed model access.

## Reference

https://fal.ai/docs/api-reference/inference-api/queue/status

## Production considerations

A queue-status request against a nonexistent UUID is
not a definitive account-entitlement test.

The diagnostic uses no billable inference submission,
but a manual Azure Container Apps Job execution may
incur Azure compute charges.

Never enable queue consumption merely to run this test.

## Azure invocation

Azure Container Apps Jobs supports per-execution
command overrides. Such overrides replace the
execution template, not the stored Job configuration.

Use the latest deployed template and modify only
the arguments for that individual execution.

Do not change the permanent default:

python worker_runtime.py --check

## Deployment

After tests pass:
1. Publish a new worker image.
2. Verify its registry digest.
3. Start one overridden diagnostic execution.
4. Inspect its status and safe logs.
5. Verify permanent worker settings remain unchanged.

No live video generation is required.

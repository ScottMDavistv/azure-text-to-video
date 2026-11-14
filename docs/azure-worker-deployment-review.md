# Azure Video Worker — Deployment Review

## Deployment status

NOT DEPLOYED.

The isolated worker template is:

`infra/worker-job.bicep`

The worker is designed for safe inspection only.

It uses a manually triggered Azure Container Apps Job.

No queue consumption or paid rendering is enabled.

## Existing Azure infrastructure

Subscription:

`41fbffdc-1793-44f9-b70f-220ea8a9bc6c`

Resource group:

`rg-textvideo-dev`

Region:

`eastus`

Existing Container Apps environment:

`cae-textvideo-dev`

Existing registry:

`acrtextvideo1794671698`

Existing API managed identity:

`id-textvideo-api-dev`

Storage account:

`sttextvideo1794671698`

Existing video queue:

`textvideo-jobs`

## Planned new resource

`Microsoft.App/jobs/job-textvideo-worker-dev`

Trigger: Manual.

Replica count for a manually triggered execution: 1.

Replica retry limit: 0.

Replica timeout: 120 seconds.

Container command:

`python worker_runtime.py --check`

There is no public HTTP ingress.

There is no Fal API secret.

There is no live video generation.

## Important identity limitation

The proposed job currently reuses the existing API
managed identity because that identity already has
limited storage and registry roles.

Before using the worker for real video rendering,
consider replacing it with a dedicated worker identity
and only the required least-privilege permissions.

## Preview instructions

The Codespaces script generates an immutable worker
image reference from the current Git commit.

To preview in Azure Cloud Shell, the worker image must
first be published to ACR with the corresponding SHA.

The private repository must be accessible through an
authenticated GitHub CLI session.

In Azure Cloud Shell:

1. Confirm the selected subscription.
2. Download scripts/cloudshell-worker-whatif.sh from GitHub.
3. Run the script with the immutable worker image.
4. Inspect the resulting Azure what-if output.

The preview script does not deploy resources.

## Approval criteria

Before any infrastructure deployment:

- The worker image exists in the expected ACR repository.
- The image tag matches the intended Git commit.
- Bicep compilation passes in Azure Cloud Shell.
- The Azure subscription and resource group are correct.
- What-if shows only the intended worker job change.
- No existing API, environment, ACR, storage or identity
  configuration is being changed.
- The job command remains `--check`.
- The trigger remains `Manual`.
- No paid provider credentials are configured.
- Expected Azure charges have been reviewed.

Do not deploy automatically.

## Later live activation

Production queue consumption requires a separate
activation and reliability review.

The current $50 monthly budget has alerting, but
application-level durable spend enforcement is not
complete.

Fal model access is not yet verified.

The standalone worker package is not a guarantee of
crash-safe video generation.

Additional testing must cover provider submission
idempotency, claim recovery, queue visibility renewal,
and payment-related failure modes.

## Production backend readiness

The API has not yet been upgraded to the latest GitHub
commit in Azure.

Actual cloud integration tests remain pending.

Public access must remain disabled until proper
user authentication and job ownership authorization
are implemented.

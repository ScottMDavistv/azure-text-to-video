# Fal API — Azure Key Vault Migration

## Objective

Move the Fal API key from a manually managed
Container Apps Job secret to an Azure Key Vault
secret reference.

The API key value must not be committed to GitHub,
embedded in Bicep, or passed as a deployment parameter.

## Current state

Worker:

job-textvideo-worker-dev

Resource group:

rg-textvideo-dev

Managed identity:

id-textvideo-api-dev

Current Container Apps Job secret:

fal-api-key

Current worker environment:

FAL_KEY -> secretRef: fal-api-key

## Target architecture

Azure Key Vault stores the Fal API key.

The Container Apps Job defines a secret named
fal-api-key with:

- keyVaultUrl: Azure Key Vault secret URI
- identity: assigned user-managed identity

The worker continues using:

FAL_KEY -> secretRef: fal-api-key

## Migration prerequisites

1. Select an appropriate Key Vault.
2. Confirm the vault is in the intended subscription.
3. Configure Azure RBAC authorization.
4. Review vault networking and access restrictions.
5. Create a secret named fal-api-key inside Key Vault.
6. Enter its value securely through the Azure Portal.
7. Grant the worker managed identity permission to
   read the Key Vault secret.
8. Confirm the secret URI is correct.
9. Review the worker's existing secret names.
10. Ensure that any other secrets are preserved.

Do not copy the credential into a GitHub file or
an unprotected shell command.

## Azure permissions

The managed identity requires permission to read
the Fal secret.

For an RBAC-enabled vault, assign the built-in
Key Vault Secrets User role at the narrowest
practical scope, preferably the specific secret.

Do not grant Key Vault Administrator to the worker.

The operator performing the migration needs separate
administrative permissions for vault setup and RBAC.

## IaC parameters

The worker template requires:

falKeyVaultSecretUri

Example format:

https://YOUR-VAULT.vault.azure.net/secrets/fal-api-key

Use a versionless URI when rotation to the latest
secret version is desired.

## Deployment gate

The Bicep template must not be applied until:

- Key Vault exists.
- Fal secret exists in Key Vault.
- Managed identity has read permission.
- Network access is verified.
- Existing secret inventory is reviewed.
- Bicep compilation succeeds.
- Azure what-if reports only approved changes.
- No existing API infrastructure changes are present.
- The worker command remains --check.
- The worker trigger remains Manual.
- Paid rendering remains disabled.

## Migration risk

A full update of the Container Apps Job configuration
can replace existing configuration collections.

The current local Container Apps secret may be
affected when switching to a Key Vault reference.

Do not apply the template before verifying the
target secret and identity permissions.

Do not rely on what-if to reveal secret values.

## Application behavior

The worker remains inspection-only.

No Fal generation will run automatically.

Successful infrastructure deployment does not
prove model entitlement or authenticated inference.

## Security notes

People with permission to start a Container Apps Job
may be able to override the execution command and
use the job's configured identities and secrets.

Restrict the job start and configuration permissions
to trusted operators.

Do not allow untrusted users to start this job.

## Rollback

Record the current job configuration and existing
secret names before migration.

If the Key Vault reference fails, do not enable
worker consumption.

Restore the previously approved configuration,
then resolve the access failure.

Avoid exporting or logging actual secret values.

## Deployment status

This document and source change do not deploy
Key Vault or update Azure resources.

Migration requires an explicit, separately reviewed
Azure deployment.

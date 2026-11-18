# Backend legacy API deployment path (documentation only)

The existing `infra/backend-app.bicep` is the repository's **source blueprint**, not evidence of a deployed revision. No unattended workflow is authorized to deploy it. The actual deployment/update path and existing Azure state must be independently verified before any rollout.

## Required operator-controlled inputs
- `jobsApiTokenSecretUri`: mandatory secure Bicep parameter referencing an **existing** high-entropy legacy `/jobs` bearer secret in Azure Key Vault. Do not commit the URI if it reveals sensitive naming conventions, and **never** commit the token value.
- `backendIdentityName`: dedicated API managed identity, distinct from the non-billable worker.
- `backendImage`: reviewed, immutable ACR `@sha256:` digest for the audited API image; the current source template's mutable tag is **not** deployment approved.
- Existing Container Apps environment, private ingress, Registry identity and storage configuration.

## Preconditions before any future supervised deployment
1. Confirm live Entra federation and API managed identity resource ID, scoped Key Vault *secret get* access, minimum necessary storage access, and minimum ACR pull access. Confirm the non-billable worker cannot retrieve this token.
2. Provision the secret using authorized private operator tooling; rotate any leaked or copied token. Validate the Key Vault URI and secret version outside logs and PRs.
3. Require audited immutable image provenance, matching Bicep compilation, approved exact-head CI and sanitized ARM what-if. Ensure no internet-facing ingress, paid-provider flag or automatic queue dispatch is enabled.
4. Verify the protected deployment environment and dedicated deployment identity RBAC. Single-owner owner approval is required for a later **separate** apply.
5. Post deployment, verify default-deny legacy /jobs, managed-identity secret reference, no secret in plaintext settings, private ingress, worker isolation, alerts, and rollback before any feature activation.

**Current status:** NOT VERIFIED. This document executes no Azure calls, provisions no token, grants no IAM rights, and authorizes no deployment or paid FAL activity.

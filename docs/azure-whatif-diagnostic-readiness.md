# Azure worker what-if — stage-specific diagnostics (development-only)

## Verified failed execution
GitHub Actions run [44000078189](https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/44000078189) completed **failure** after successful protected-main dispatch, OIDC subject validation and Azure login. [Artifact 14290809507](https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/44000078189/artifacts/14290809507) records `blocked-unverified`, with the generic `Azure operation unavailable or not permitted by current identity`. It **does not identify** whether the failure was during reading the existing job or ARM what-if, or whether the cause was RBAC, tool availability or template validation. No permission grant is justified by that evidence alone.

## Diagnostic change
A new bounded `diagnostic_stage` value records only one of the following hardcoded stages: `preflight`, `template-validation`, `account-inspection`, `worker-resource-read`, `worker-baseline-check`, `arm-whatif-preview`, `image-only-diff-classification`. Azure CLI stderr/stdout, token/identity claims, request/response payloads, private environment values and resource details are still excluded from logs and artifacts. The workflow still fails closed and emits a sanitized artifact.

### Interpretation and next decision
- `worker-resource-read`: may be lack of resource-read permission, unsupported API version, Azure CLI or network issue; **not** proof of missing ARM what-if permission.
- `worker-baseline-check`: current worker differs from strict expectation (e.g., image/flags/command/profile); inspect only approved non-secret fields before changing template.
- `arm-whatif-preview`: ARM preview operation failed; can reflect `whatIf/action` or deployment validation/Bicep issues. Investigate further before proposing scoped RBAC.
- `image-only-diff-classification`: what-if returned a change other than exactly one old-tag-to-pinned-digest image delta; **do not relax classifier**.

After independent review and explicit authorized merge of the diagnostics-only PR, the owner can manually dispatch **Manual Azure Worker What-If (No Deployment)** again on protected `main`. No automatic dispatch, Azure write grant or deployment has been approved.

## Immutable provenance and remaining gates
- ACR tag-to-manifest lookup: [Reader audit run 43983848946](https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43983848946) produced digest `sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646`. This establishes registry metadata only, **not** independent source attestation, signature, vulnerability scan, ACR pull rights or retained rollback image.
- PR #37 remains draft and unmerged. Deployment worker must remain Manual/Consumption with `worker_runtime.py --check`, disabled FAL and worker consumption.
- PR #24 uses a **mutable Git SHA image tag in its what-if and deploy workflow**. This conflicts with the immutable digest required by PR #37 and must be corrected on a separate review branch before any merge.
- PR #28 defines a scheduled reconciler even in `--check` mode (96/day by default), and requires unknown dedicated identity, ACR pull, storage RBAC, actual alert log schema, action group delivery and cost evidence. Never infer a live resource or free execution from Bicep alone.

## Release blockers
1. Safely identify the exact failing CLI stage in a re-run.
2. Evaluate ARM what-if permission **without** granting anything as part of this work.
3. Complete live image-only what-if; prove no non-image drift.
4. Resolve security/governance and image provenance/pull/rollback gates.
5. Obtain separate manual approvals for workflow merges, IAM (if ultimately necessary), and any deployment.

No Azure writes, identity changes, worker start, merge, provider billing, or paid FAL calls were performed.

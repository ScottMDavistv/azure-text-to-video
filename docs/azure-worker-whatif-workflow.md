# Manual Azure worker what-if — isolated, non-deploying

This workflow implements the **authorized preparation** for an isolated Azure
ARM what-if for `job-textvideo-worker-dev` in `rg-textvideo-dev`, using
the **existing** GitHub Reader OIDC identity only.

## How it is gated

- GitHub Action: `.github/workflows/azure-worker-whatif.yml`
- A pull request runs mocked offline regressions only; **never** logs into Azure.
- The live job is possible **only from an explicitly manually dispatched run
  on protected `main`**, gated by `AZURE_AUDIT_OPERATIONS_ENABLED=true`.
- It uses the existing `azure-readonly-audit` GitHub environment, exact OIDC
  subject/repository ID checks, and pinned third-party Actions.
- It does not use the dedicated deployment identity. It does not request or
  grant RBAC, create or update Azure deployments, start jobs, push ACR images,
  activate paid video generation, or retrieve Azure secrets.
- Dynamic Azure CLI extension installation and CLI telemetry are disabled.
- The audit's previously established Reader permission is **not** proof of
  ARM what-if permission. A missing `Microsoft.Resources/deployments/whatIf/action`
  permission causes a **blocked** report; no identity fallback is attempted.

## Immutable inputs

The job checks out worker Bicep from reviewed PR #37 commit
`e5e1bcbd0a9cb270957febb71faf884d8fc0b649`.
No user-supplied branch/ref, image, deployment name, scope or arbitrary CLI
arguments exist. The exact verified ACR image is:

```text
acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646
```

Evidence: GitHub audit run
https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43983848946
and its sanitized artifact 14283842942.

## Evidence and blocking logic

The job reads the exact Azure subscription, worker resource and disabled
configuration; then issues only `az deployment group what-if`.
It classifies the resulting JSON **without publishing the raw response**.

Only one approved delta is accepted: modification of
`properties.template.containers[0].image` from the existing Git-SHA tag to
the above pinned OCI digest. Any additional resource, identity, configuration,
secrets, scale, command, image or unexpected/ambiguous what-if output **blocks**.
A what-if output with no changes is also blocked, pending investigation.

The 14-day `azure-worker-whatif-evidence` artifact contains only sanitized
JSON and Markdown results. A green workflow signifies that an image-only
preview was confirmed, **not** approval to deploy. Errors, unsupported output
shapes, insufficient permissions, timeouts or unsafe changes cause failed CI
with `blocked-unverified` in the evidence.

## Activation procedure — not performed by this PR

1. Independently review and explicitly authorize **this workflow PR's merge**
   under single-owner security governance. Keep PR #37 draft/unmerged unless
   separately authorized.
2. Confirm the GitHub `azure-readonly-audit` environment is limited to
   `main` and its existing Reader OIDC federation remains unchanged.
3. In GitHub Actions select **Manual Azure Worker What-If (No Deployment)**
   and explicitly **Run workflow** on `main`. There are no input fields.
4. Inspect the workflow and download its `azure-worker-whatif-evidence`
   artifact. If the run fails due to insufficient permission, do not grant
   write RBAC or switch to a deployment identity as part of this task.
5. Before any future deployment or merge authorization, independently review
   digest provenance, registry pull rights, rollback retention, full what-if
   behavior, branch protections, finance/provider controls and IAM scope.

A successful offline PR test is **not** a live Azure what-if.
The preview does not update the deployed worker, which retains Manual,
Consumption, `worker_runtime.py --check`, disabled provider and consumption,
and no worker secrets.

# PR #24 to #44: Azure deployment governance consolidation

**Canonical draft:** PR #44. **Original:** PR #24 retained open/draft. This document is a code-source reconciliation record, not live Azure approval.

## Preservation matrix

| Original #24 | #44 candidate | Finding |
|---|---|---|
| Disable legacy broad `infra/main.bicep` apply | `.github/workflows/deploy-infrastructure.yml` | Preserved: manually triggered legacy workflow fails closed with no Azure credentials or deployment |
| Separate read-only preview vs supervised apply identities | `guarded-azure-worker-deploy.yml` | Preserved: distinct `AZURE_PREVIEW_CLIENT_ID` and `AZURE_DEPLOY_CLIENT_ID`, isolated environments |
| Main-only SHA-bound supervised deployment confirmation | Guarded workflow deploy branch | Preserved explicit reviewed SHA, owner actor, exact confirmation and protected apply environment; no automatic apply |
| Disabled-by-default deployment flags | `AZURE_DEV_DEPLOY_ENABLED`, `PROTECTED_MAIN_VERIFIED` | Preserved; activation is an external, separately authorized decision |
| Worker config and finance gates | `verify_azure_worker_safe.py`, `check_azure_deployment_governance.py` | Preserved static checks, security workflow now also runs governance guard |
| Immutable worker image | Guarded what-if and deploy paths | Strengthened: verified OCI `@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646` instead of mutable tag |
| Manual Azure diagnostics | Existing main `azure-worker-whatif.yml`, PR #47 draft | **Separate from #44**: current live ARM preview failed; never switch to privileged deploy identity as workaround |

## Known deployment limitations

The staged deployment workflow still has a path capable of Azure writes **only after an explicitly configured deployment identity with write RBAC is created/assigned, environment protections and activation flags are independently verified, and manual dispatch is approved**. That combination is **not** authorized here; no rights are provisioned by #44. The read-only what-if run #44002298041 failed at `arm-whatif-preview` for a reason not yet confirmed. The image manifest metadata does not prove signature, provenance, pull rights, or rollback retention. GitHub environment reviewer independence is not established. The workflows must pass an independent security-focused diff review before any merge.

## Disposition

No file from the original six-file #24 change set is omitted by #44. Preserve #44 as one draft canonical implementation; **keep #24 open and draft** for traceability, without merging or closing. No IAM change, Azure run, FAL activation, finance adjustment or automatic dispatch.

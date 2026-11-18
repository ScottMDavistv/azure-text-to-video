# Worker image digest verification and rollback (development-only)

## Scope and provenance
The active development Container Apps Job `job-textvideo-worker-dev` currently refers to
`acrtextvideo1794671698.azurecr.io/textvideo-worker:692bb1b7f7bd467f1d64e7288115424d21506c12`.
This is a **mutable tag**, even though its spelling is a Git commit SHA. The tag was resolved through the dedicated Reader OIDC audit on 2026-11-16;
manifest digest: `sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646` (metadata-level evidence only).

The code and offline regression tests in this PR do not change the deployed Job,
run a container, upload an image, enable rendering, add RBAC, or activate spending.

## Verified ACR metadata baseline

- GitHub Actions run: https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43983848946
- Evidence artifact: https://github.com/ScottMDavistv/azure-text-to-video/actions/runs/43983848946/artifacts/14283842942
- Registry/repository: `acrtextvideo1794671698/textvideo-worker`
- Manifest digest: `sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646`
- Pinned image: `acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646`
- Audit records `verified-metadata` only; independent provenance, signature and pull authorization have **not** been proven.
- Deployed job still points to mutable source tag and remains disabled/manual.

## Read-only ACR evidence (must precede any image change)
Use a principal with existing **ACR manifest metadata read** permission. Local Azure
Reader at ARM scope does not prove ACR repository data-plane access. From a trusted
GitHub Actions job or an approved read-only session:

```bash
python scripts/worker-image-digest.py --resolve-tag 692bb1b7f7bd467f1d64e7288115424d21506c12
```

This executes only `az acr manifest show-metadata --registry acrtextvideo1794671698
--name textvideo-worker:<tag> --output json`. It validates the canonical manifest
`sha256` format and prints the expected pinned image reference. On missing digest,
CLI error, invalid metadata, or insufficient permissions it **fails closed**. It does
not change ACR or Azure. Capture the registry, repository, tag, manifest digest,
UTC time, repository revision, principal identity/scope, Azure CLI version, and
sanitized command result in the review evidence. Confirm the same digest against
an independent authenticated ACR manifest inspection before deployment; OCI
manifest digest syntax alone does not establish content trust or image origin.

For an offline reference check:
```bash
python scripts/worker-image-digest.py --validate \
  'acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:<64-lowercase-hex>'
```
Replace the placeholder with the actual read-only verified value; do **not**
use a placeholder during deployment. An ACR tag-to-digest lookup may change if
the tag is repointed, so verify the current manifest again before a deployment.

## Development configuration and deployment guard
`infra/worker-job.bicep` pins its `workerImage` parameter with an exact `@allowed` digest value and must receive an
`acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:...` reference.
`scripts/cloudshell-worker-whatif.sh` rejects all references other than the reviewed digest and requires an immutable source commit SHA.
This is a what-if tool **only**, not a deployment script. Users invoking Bicep
directly must independently validate the digest. Do not mistake a Git SHA tag
as an immutable image reference or treat a successful CI run as manifest evidence.

The existing worker remains Manual/Consumption, with command
`python worker_runtime.py --check`, provider disabled, worker queue consumption
false, and live rendering false. No paid FAL calls or Azure write actions are
permitted for this task. Protected main and PR #24/#28 deployment safeguards
remain intact.

## Rollback (requires separate approved deployment)
1. Before a future authorized change, record the currently deployed worker image
   and all its current safe settings and export the sanitized ARM configuration.
   Retain its manifest digest while still available in ACR (if current image
   is tag-only, resolve its digest **before** making any change).
2. Verify both the proposed and rollback digests still resolve to the expected
   `textvideo-worker` repository and are not subject to retention/garbage collection.
3. Review Azure what-if for the isolated worker Job against the pinned image
   and explicitly confirm no identity, trigger, command, env, secrets, scale,
   provider or spending changes. Obtain security-sensitive single-owner approval.
4. During an independently authorized maintenance/deployment action, change
   **only** `workerImage` to the selected approved digest.
5. If the pinned image fails verification, obtain new authorization to restore
   the previously recorded digest. Re-run what-if and post-deployment **read-only**
   audit. Do not roll back to a mutable tag without separate review.
6. If ACR does not contain the previous manifest, stop. Never invent or recreate
   a digest, deploy a different image under the old tag, auto-approve, or grant
   write RBAC merely to force completion.

## Remaining gates
- Actual ACR manifest metadata: **VERIFIED** on 2026-11-16, subject to revalidation before deployment.
- ACR metadata read under existing Reader identity: **VERIFIED**.
- Independent provenance/manifest inspection, image pull and full Azure what-if:
  **NOT VERIFIED**.
- Azure write RBAC remains unassigned. No PR merge or deployment is authorized.

## Isolated Azure what-if (prepared, NOT executed)

Run only after separate explicit authorization for an Azure ARM what-if and verification of its RBAC requirements. The existing dedicated audit Reader is **not** automatically authorized to perform deployment what-if. Do not grant Azure write permissions for this task.

Use a reviewed immutable commit SHA that contains the current Bicep template and this script, and confirm its exact commit hash in PR #37. Then invoke:

```bash
bash scripts/cloudshell-worker-whatif.sh 'acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646' '<REVIEWED_40_CHARACTER_GIT_COMMIT_SHA>'
```

The script checks the subscription, downloads only that reviewed template revision, compiles Bicep, and calls `az deployment group what-if` **without create/apply/start commands**. Review diff must show an image-only update to the existing worker. Any resource replacement, additional creates/deletes, changed identity, registry credentials, Manual trigger, Consumption, inspection-only command, disabled flags, or other values is a blocking unexpected change. Do not interpret Bicep compilation or digest-metadata verification as a successful what-if. Capture review evidence separately.

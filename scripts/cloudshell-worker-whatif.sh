#!/usr/bin/env bash
set -euo pipefail

# Run in Azure Cloud Shell (Bash), not Codespaces.
#
# This script downloads an isolated template from the
# private GitHub repository and runs Azure WHAT-IF ONLY.
#
# It cannot deploy the worker or start executions.

SUBSCRIPTION="41fbffdc-1793-44f9-b70f-220ea8a9bc6c"
RESOURCE_GROUP="rg-textvideo-dev"
REPOSITORY="ScottMDavistv/azure-text-to-video"
REGISTRY="acrtextvideo1794671698.azurecr.io"

IMAGE="${1:-}"
REVIEWED_TEMPLATE_COMMIT="${2:-}"
VERIFIED_IMAGE="acrtextvideo1794671698.azurecr.io/textvideo-worker@sha256:219ebbe03d9b1356a4091912b4955c1987ba8a0131a4e084953ab3796b5f0646"

if [[ -z "$IMAGE" ]]; then
    echo "Usage: $0 <immutable-worker-image-reference>"
    exit 2
fi

if [[ "$IMAGE" != "$VERIFIED_IMAGE" ]]; then
    echo "ERROR: Worker image does not match the exact audited ACR manifest digest"
    exit 2
fi

if [[ ! "$REVIEWED_TEMPLATE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: Reviewed immutable Git commit SHA required as second argument"
    exit 2
fi

command -v az >/dev/null
command -v gh >/dev/null

CURRENT_SUBSCRIPTION="$(az account show --query id -o tsv)"

if [[ "$CURRENT_SUBSCRIPTION" != "$SUBSCRIPTION" ]]; then
    echo "ERROR: Wrong Azure subscription"
    echo "Expected subscription: $SUBSCRIPTION"
    exit 3
fi

echo "=== Checking resource group ==="

az group show \
    --name "$RESOURCE_GROUP" \
    --query "{name:name,location:location}" \
    --output json

echo "=== Downloading isolated worker template ==="

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

gh api \
    "repos/$REPOSITORY/contents/infra/worker-job.bicep?ref=$REVIEWED_TEMPLATE_COMMIT" \
    -H "Accept: application/vnd.github.raw+json" \
    > "$TEMP_DIR/worker-job.bicep"

test -s "$TEMP_DIR/worker-job.bicep"

# Fail closed if the supplied Git revision does not contain the reviewed
# immutable-image allowlist and safe worker configuration. A 40-hex SHA alone
# is not proof that an earlier template is safe to preview.
grep -Fq "@allowed([" "$TEMP_DIR/worker-job.bicep"
grep -Fq "'$VERIFIED_IMAGE'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "triggerType: 'Manual'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "workloadProfileName: 'Consumption'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "'--check'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "name: 'VIDEO_RENDER_PROVIDER'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "name: 'ENABLE_WORKER_CONSUMPTION'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "name: 'ENABLE_LIVE_VIDEO_RENDERING'" "$TEMP_DIR/worker-job.bicep"
grep -Fq "secrets: []" "$TEMP_DIR/worker-job.bicep"

echo "=== Checking reviewed immutable Bicep template syntax ==="

az bicep build \
    --file "$TEMP_DIR/worker-job.bicep" \
    --outfile "$TEMP_DIR/worker-job.json"

echo "=== Review-only Azure deployment what-if (digest syntax checked; verify ACR provenance separately) ==="

az deployment group what-if \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$TEMP_DIR/worker-job.bicep" \
    --parameters workerImage="$IMAGE" \
    --result-format FullResourcePayloads

echo "=============================================="
echo "AZURE WHAT-IF COMPLETE"
echo "NO DEPLOYMENT EXECUTED"
echo "REVIEW CHANGES BEFORE ANY DEPLOYMENT"
echo "=============================================="

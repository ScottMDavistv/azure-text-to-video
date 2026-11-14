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

if [[ -z "$IMAGE" ]]; then
    echo "Usage: $0 <immutable-worker-image-reference>"
    exit 2
fi

if [[ ! "$IMAGE" =~ ^acrtextvideo1794671698\.azurecr\.io/textvideo-worker:[0-9a-f]{40}$ ]]; then
    echo "ERROR: Expected immutable worker image with full Git SHA"
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
    "repos/$REPOSITORY/contents/infra/worker-job.bicep" \
    -H "Accept: application/vnd.github.raw+json" \
    > "$TEMP_DIR/worker-job.bicep"

test -s "$TEMP_DIR/worker-job.bicep"

echo "=== Checking Bicep syntax ==="

az bicep build \
    --file "$TEMP_DIR/worker-job.bicep" \
    --outfile "$TEMP_DIR/worker-job.json"

echo "=== Azure read-only deployment what-if ==="

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

#!/usr/bin/env bash
# B2 read-only resource and principal-specific audit. No writes or data access.
set -euo pipefail
RG="rg-textvideo-dev"
ACCOUNT="sttextvideo1794671698"
API="ca-textvideo-api-dev"
WORKER="job-textvideo-worker-dev"
az account show --query '{state:state,tenantId:tenantId}' -o json
STORAGE_ID="$(az storage account show -g "$RG" -n "$ACCOUNT" --query id -o tsv)"
API_ID="$(az resource show -g "$RG" -n "$API" --resource-type 'Microsoft.App/containerApps' --query id -o tsv)"
WORKER_ID="$(az resource show -g "$RG" -n "$WORKER" --resource-type 'Microsoft.App/jobs' --query id -o tsv)"
API_PRINCIPAL="$(az resource show --ids "$API_ID" --query "identity.userAssignedIdentities.*.principalId | [0]" -o tsv)"
WORKER_PRINCIPAL="$(az resource show --ids "$WORKER_ID" --query "identity.userAssignedIdentities.*.principalId | [0]" -o tsv)"
if [[ -z "$API_PRINCIPAL" || -z "$WORKER_PRINCIPAL" ]]; then
  echo "::error::Missing API or worker identity"
  exit 1
fi
if [[ "$API_PRINCIPAL" == "$WORKER_PRINCIPAL" ]]; then
  echo "::warning::API and worker share the same principal; least-privilege separation pending."
fi
echo "=== Resource-scoped identity details (no tokens) ==="
az resource show --ids "$API_ID" --query '{name:name,identity:identity.type}' -o json
az resource show --ids "$WORKER_ID" --query '{name:name,identity:identity.type,trigger:properties.configuration.triggerType}' -o json
echo "=== Storage data RBAC: effective assignments for runtime principals ==="
for principal in "$API_PRINCIPAL" "$WORKER_PRINCIPAL"; do
  echo "--- Principal $principal ---"
  az role assignment list --assignee-object-id "$principal" --scope "$STORAGE_ID" --include-inherited --query '[].{role:roleDefinitionName,scope:scope,principalId:principalId}' -o json
done
echo "=== Storage services — configured tables, queues and containers (ARM only) ==="
for service in tableServices/default/tables queueServices/default/queues blobServices/default/containers; do
  echo "--- $service ---"
  az rest --method get --url "https://management.azure.com${STORAGE_ID}/${service%%/*}/default/${service#*/default/}?api-version=2023-01-01" --query 'value[].name' -o json || echo "::warning::Cannot read $service from ARM"
done
echo "=== Entra app configuration status ==="
echo "Graph directory reads are a separate permission scope; existing Reader role does not grant them."
echo "B2 READ-ONLY AUDIT COMPLETE"

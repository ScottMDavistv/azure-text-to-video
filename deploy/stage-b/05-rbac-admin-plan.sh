#!/usr/bin/env bash
# B2: output administrator-reviewed role assignment commands; NEVER executes writes.
# Run in Azure Cloud Shell only after operator confirms the identity separation plan.
set -euo pipefail
RG="rg-textvideo-dev"
ACCOUNT="sttextvideo1794671698"
API_IDENTITY="id-textvideo-api-dev"
WORKER_IDENTITY="id-textvideo-worker-dev"
STORAGE_ID="$(az storage account show -g "$RG" -n "$ACCOUNT" --query id -o tsv)"
API_PRINCIPAL="$(az identity show -g "$RG" -n "$API_IDENTITY" --query principalId -o tsv)"
echo "B2 PROPOSAL ONLY -- no Azure writes will be made."
echo "Existing API principal: $API_PRINCIPAL"
if ! az identity show -g "$RG" -n "$WORKER_IDENTITY" --query principalId -o tsv >/dev/null 2>&1; then
  echo "WORKER IDENTITY MISSING: an administrator must create $WORKER_IDENTITY"
  echo "After approval: az identity create -g $RG -n $WORKER_IDENTITY -l eastus"
  echo "Then attach worker identity to job-textvideo-worker-dev during a separate reviewed rollout."
  echo "NO worker permissions can be assigned until this identity exists."
else
  echo "Worker identity already exists; verify it is actually attached to the worker before assigning permissions."
fi
echo "=== Proposed API assignments (review before execution) ==="
echo "az role assignment create --assignee-object-id '$API_PRINCIPAL' --assignee-principal-type ServicePrincipal --role 'Storage Table Data Contributor' --scope '$STORAGE_ID/tableServices/default/tables/VideoJobs'"
echo "az role assignment create --assignee-object-id '$API_PRINCIPAL' --assignee-principal-type ServicePrincipal --role 'Storage Queue Data Message Sender' --scope '$STORAGE_ID/queueServices/default/queues/textvideo-jobs'"
echo "=== Worker assignments (only after identity split and rollout review) ==="
echo "Set WORKER_PRINCIPAL from az identity show for $WORKER_IDENTITY."
echo "Storage Table Data Contributor on $STORAGE_ID/tableServices/default/tables/VideoJobs"
echo "Storage Queue Data Message Processor on $STORAGE_ID/queueServices/default/queues/textvideo-jobs"
echo "Storage Blob Data Contributor on $STORAGE_ID/blobServices/default/containers/videos"
echo "Do not grant GitHub audit identity any writer/Contributor role."
echo "This plan has not assigned roles or created resources."

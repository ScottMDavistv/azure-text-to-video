#!/usr/bin/env bash
# Stage B1/B2 READ-ONLY inventory; never modifies Azure or Entra.
set -euo pipefail

command -v az >/dev/null || { echo "Azure CLI is required" >&2; exit 1; }
az account show --query '{subscription:id,tenant:tenantId,state:state}' -o json
SUB_ID="$(az account show --query id -o tsv)"
if [[ -z "$SUB_ID" ]]; then echo "No subscription selected" >&2; exit 1; fi

ACCOUNT="sttextvideo1794671698"
API="ca-textvideo-api-dev"
WORKER="job-textvideo-worker-dev"
IDENTITY="id-textvideo-api-dev"
ACR="acrtextvideo1794671698"
VAULT="kv-textvideo-1794671698"
echo "=== Resource discovery (read-only) ==="
for name in "$ACCOUNT" "$API" "$WORKER" "$IDENTITY" "$ACR" "$VAULT"; do
  echo "--- $name ---"
  az resource list --name "$name" --query '[].{id:id,name:name,type:type,resourceGroup:resourceGroup,location:location}' -o json
done

echo "=== API managed identity ==="
API_ID="$(az resource list --name "$API" --resource-type Microsoft.App/containerApps --query '[0].id' -o tsv)"
if [[ -n "$API_ID" ]]; then
  az containerapp identity show --ids "$API_ID" -o json
  echo "=== API runtime settings (NAMES ONLY; never print values) ==="
  az containerapp show --ids "$API_ID" --query 'properties.template.containers[].env[].name' -o json
fi

echo "=== Worker managed identity ==="
WORKER_ID="$(az resource list --name "$WORKER" --resource-type Microsoft.App/jobs --query '[0].id' -o tsv)"
if [[ -n "$WORKER_ID" ]]; then
  az containerapp job show --ids "$WORKER_ID" --query '{name:name,identity:identity,trigger:properties.configuration.triggerType}' -o json
fi

echo "=== User-assigned identity ==="
MI_ID="$(az resource list --name "$IDENTITY" --resource-type Microsoft.ManagedIdentity/userAssignedIdentities --query '[0].id' -o tsv)"
if [[ -n "$MI_ID" ]]; then
  az identity show --ids "$MI_ID" --query '{id:id,principalId:principalId,clientId:clientId}' -o json
fi

echo "=== Storage security settings ==="
STORAGE_ID="$(az resource list --name "$ACCOUNT" --resource-type Microsoft.Storage/storageAccounts --query '[0].id' -o tsv)"
if [[ -n "$STORAGE_ID" ]]; then
  az storage account show --ids "$STORAGE_ID" --query '{id:id,allowSharedKeyAccess:allowSharedKeyAccess,minimumTlsVersion:minimumTlsVersion,publicNetworkAccess:publicNetworkAccess,networkRuleSet:networkRuleSet.defaultAction}' -o json
fi

echo "=== Role assignment inventory (scopes and role names; no tokens) ==="
for scope in "$STORAGE_ID" "$MI_ID"; do
  if [[ -n "$scope" ]]; then
    az role assignment list --scope "$scope" --include-inherited --fill-principal-name false --query '[].{principalId:principalId,role:roleDefinitionName,scope:scope}' -o json
  fi
done

echo "=== Entra app registration discovery ==="
az ad app list --display-name "Video Render Tool" --query '[].{id:id,appId:appId,displayName:displayName,signInAudience:signInAudience}' -o json
az ad app list --display-name "Azure Text-to-Video" --query '[].{id:id,appId:appId,displayName:displayName,signInAudience:signInAudience}' -o json
echo "INVENTORY COMPLETE — no Azure changes made."

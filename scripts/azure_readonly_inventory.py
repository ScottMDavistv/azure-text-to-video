#!/usr/bin/env python3
"""Read-only Azure inventory. Requires an already provisioned Reader-scoped OIDC identity.

Strict allowlist: ARM GET/list only, one resource group, no secrets or key reads.
"""
import json
import os
import re
import subprocess

ALLOWED_TYPES = {
    "Microsoft.App/containerApps",
    "Microsoft.App/jobs",
    "Microsoft.App/managedEnvironments",
    "Microsoft.ContainerRegistry/registries",
    "Microsoft.Storage/storageAccounts",
    "Microsoft.KeyVault/vaults",
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.OperationalInsights/workspaces",
}

def safe_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9_.()-]{1,90}", value or ""):
        raise ValueError("Invalid resource group identifier")
    return value

def inventory(resources, group):
    filtered = []
    for item in resources:
        if item.get("type") not in ALLOWED_TYPES:
            continue
        filtered.append({
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "location": item.get("location", ""),
            "provisioning_state": (item.get("properties") or {}).get("provisioningState", "unknown"),
        })
    return {"status": "inventory only — runtime health not asserted",
            "resource_group": group,
            "resources": sorted(filtered, key=lambda x: (x["type"], x["name"]))}

def main():
    group = safe_name(os.environ.get("AZURE_AUDIT_RESOURCE_GROUP", ""))
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", subscription):
        raise SystemExit("AZURE_SUBSCRIPTION_ID must be an explicit subscription UUID")
    command = ["az", "resource", "list", "--resource-group", group,
               "--subscription", subscription, "--output", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=True)
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Azure resource response")
    output = inventory(data, group)
    with open("azure-inventory.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Inventory completed, count:", len(output["resources"]))

if __name__ == "__main__":
    main()

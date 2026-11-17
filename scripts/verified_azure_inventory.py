"""Fail-closed GitHub artifact handoff for sanitized, protected-main Azure Reader inventory.

The publisher has actions:read, not Azure OIDC. GitHub metadata authenticates the
origin; absence, incomplete Reader job, old runs or malformed data never verifies Azure.
"""
import datetime as dt
import io
import json
import re
import urllib.request
import urllib.parse
import zipfile

WORKFLOW = ".github/workflows/azure-readonly-operations.yml"
ARTIFACT = "azure-readonly-operations"
GROUP = "rg-textvideo-dev"
TYPES = frozenset({
    "Microsoft.App/containerApps", "Microsoft.App/jobs",
    "Microsoft.App/managedEnvironments", "Microsoft.ContainerRegistry/registries",
    "Microsoft.Storage/storageAccounts", "Microsoft.KeyVault/vaults",
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.OperationalInsights/workspaces",
})
MAX_AGE_SECONDS = 24 * 60 * 60
MAX_COMPRESSED = 3 * 1024 * 1024
MAX_UNCOMPRESSED = 1024 * 1024


class NoVerifiedInventory(Exception):
    pass


class StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlparse(newurl)
        if url.scheme != "https":
            raise NoVerifiedInventory("Non-HTTPS artifact redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def api_get(path, token, *, binary=False):
    if not path.startswith("/repos/"):
        raise NoVerifiedInventory("Unrecognized GitHub request")
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "video-render-inventory-handoff"},
    )
    opener = urllib.request.build_opener(StripAuthOnRedirect())
    with opener.open(request, timeout=25) as response:
        data = response.read(MAX_COMPRESSED + 1 if binary else MAX_UNCOMPRESSED + 1)
    if len(data) > (MAX_COMPRESSED if binary else MAX_UNCOMPRESSED):
        raise NoVerifiedInventory("Oversized GitHub response")
    return data if binary else json.loads(data)


def timestamp(value):
    if not isinstance(value, str):
        raise NoVerifiedInventory("Missing timestamp")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(dt.timezone.utc)
    except ValueError:
        raise NoVerifiedInventory("Invalid timestamp") from None


def validate_inventory(payload):
    if not isinstance(payload, dict) or set(payload) != {"status", "resource_group", "resources"}:
        raise NoVerifiedInventory("Unrecognized inventory schema")
    if payload["resource_group"] != GROUP or payload["status"] != "inventory only — runtime health not asserted":
        raise NoVerifiedInventory("Unrecognized inventory scope or status")
    resources = payload["resources"]
    if not isinstance(resources, list) or len(resources) > 100:
        raise NoVerifiedInventory("Unrecognized inventory resource list")
    for item in resources:
        if (not isinstance(item, dict) or
                set(item) != {"name", "type", "location", "provisioning_state"} or
                item["type"] not in TYPES or
                any(not isinstance(v, str) or len(v) > 200 for v in item.values())):
            raise NoVerifiedInventory("Invalid inventory resource")
    return payload


def extract_inventory(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        matches = [entry for entry in archive.infolist() if entry.filename == "azure-inventory.json"]
        if len(matches) != 1 or matches[0].file_size > MAX_UNCOMPRESSED:
            raise NoVerifiedInventory("Missing or oversized inventory artifact")
        with archive.open(matches[0]) as source:
            raw = source.read(MAX_UNCOMPRESSED + 1)
        if len(raw) > MAX_UNCOMPRESSED:
            raise NoVerifiedInventory("Oversized inventory payload")
        return validate_inventory(json.loads(raw))


def load(repo, token, *, read=api_get, now=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or "") or not token:
        raise NoVerifiedInventory("Invalid repository or authorization")
    now = now or dt.datetime.now(dt.timezone.utc)
    base = "/repos/" + repo
    runs = read(base + "/actions/workflows/azure-readonly-operations.yml/runs?branch=main&per_page=10", token)
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        raise NoVerifiedInventory("Missing trusted run metadata")
    for run in runs["workflow_runs"]:
        if (not isinstance(run, dict) or run.get("conclusion") != "success" or
                run.get("head_branch") != "main" or run.get("event") not in
                ("schedule", "workflow_dispatch", "push") or
                run.get("path") != WORKFLOW or
                (run.get("repository") or {}).get("full_name", "").casefold() != repo.casefold()):
            continue
        age = (now - timestamp(run.get("created_at"))).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS:
            continue
        ident = run.get("id")
        if not isinstance(ident, int) or ident <= 0:
            continue
        jobs = read(base + "/actions/runs/" + str(ident) + "/jobs?per_page=100", token)
        if not isinstance(jobs, dict) or not any(
            job.get("name") == "Reader-only Azure preflight" and job.get("conclusion") == "success"
            for job in jobs.get("jobs", []) if isinstance(job, dict)
        ):
            continue
        artifacts = read(base + "/actions/runs/" + str(ident) + "/artifacts?per_page=100", token)
        if not isinstance(artifacts, dict):
            continue
        matches = [a for a in artifacts.get("artifacts", []) if isinstance(a, dict)
                   and a.get("name") == ARTIFACT and not a.get("expired")]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
            continue
        content = read(base + "/actions/artifacts/" + str(matches[0]["id"]) + "/zip",
                       token, binary=True)
        verified = extract_inventory(content)
        return verified, {"run_id": ident, "head_sha": run.get("head_sha"), "created_at": run["created_at"]}
    raise NoVerifiedInventory("No fresh successful protected-main Reader inventory")

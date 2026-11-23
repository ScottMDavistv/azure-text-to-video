"""Read-only GitHub App provenance verification. No approvals, writes or dispatch."""
from __future__ import annotations
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlsplit
from urllib.error import HTTPError, URLError

REPO = "ScottMDavistv/azure-text-to-video"
SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"\A(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\Z")
MAX_PAGES = 30
MAX_BYTES = 2_000_000


class EvidenceError(ValueError):
    pass


def strict_json(raw):
    """Reject duplicate keys and non-finite constants in security evidence."""
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise EvidenceError("ambiguous duplicate JSON field")
            result[name] = value
        return result

    def invalid_constant(_value):
        raise EvidenceError("nonstandard JSON number")

    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvidenceError("invalid JSON evidence") from exc


def verify_webhook(body: bytes, signature: str, secret: bytes, delivery: str) -> str:
    """HMAC authenticates bytes; replay prevention REQUIRES external durable ledger."""
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise EvidenceError("invalid payload")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise EvidenceError("missing webhook secret")
    if not isinstance(signature, str) or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise EvidenceError("malformed signature")
    if not isinstance(delivery, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", delivery):
        raise EvidenceError("invalid delivery id")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise EvidenceError("webhook signature mismatch")
    return delivery.lower()


def safe_path(path: str) -> str:
    if (not isinstance(path, str) or not PATH.fullmatch(path) or
            any(part in ("", ".", "..") for part in path.split("/")) or
            not path.startswith(".github/workflows/") or
            not path.endswith((".yml", ".yaml"))):
        raise EvidenceError("untrusted workflow path")
    return path


def sha(s):
    if not isinstance(s, str) or not SHA.fullmatch(s):
        raise EvidenceError("invalid SHA")
    return s


@dataclass(frozen=True)
class WorkflowRule:
    name: str
    path: str
    trusted_blob_sha: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not 0 < len(self.name) <= 140:
            raise EvidenceError("invalid workflow name")
        safe_path(self.path)
        sha(self.trusted_blob_sha)


class GitHubReadOnly:
    """Scoped authenticated GET transport. Installation token supplied out-of-band."""
    def __init__(self, installation_token: str, opener=urlopen):
        if not isinstance(installation_token, str) or len(installation_token) < 20:
            raise EvidenceError("installation token required")
        self._token = installation_token
        self._opener = opener

    def get(self, path: str):
        if not isinstance(path, str) or not path.startswith("/repos/" + REPO + "/"):
            raise EvidenceError("out-of-scope API path")
        parsed = urlsplit(path)
        if (parsed.scheme or parsed.netloc or parsed.fragment or "%2f" in path.lower() or
                any(part in ("", ".", "..") for part in parsed.path.split("/")[1:])):
            raise EvidenceError("unsafe API path")
        req = Request("https://api.github.com" + path, method="GET", headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "video-render-bridge-evidence/0.1",
        })
        try:
            with self._opener(req, timeout=15) as res:
                status, raw = res.status, res.read(MAX_BYTES + 1)
                headers = {k.lower(): v for k, v in res.headers.items()}
        except (HTTPError, URLError, OSError) as exc:
            raise EvidenceError("GitHub API unavailable") from exc
        if status != 200 or len(raw) > MAX_BYTES:
            raise EvidenceError("invalid response")
        return strict_json(raw), headers

    def pages(self, path: str, *, collection=None):
        seen, items, total = set(), [], None
        origin = urlsplit(path)
        filters = sorted(parse_qsl(origin.query, keep_blank_values=True))
        for _ in range(MAX_PAGES):
            if path in seen:
                raise EvidenceError("pagination cycle")
            seen.add(path)
            obj, headers = self.get(path)
            if collection is not None:
                if (not isinstance(obj, dict) or type(obj.get("total_count")) is not int
                        or obj["total_count"] < 0
                        or (total is not None and total != obj["total_count"])):
                    raise EvidenceError("invalid or changed collection count")
                total = obj["total_count"]
                obj = obj.get(collection)
            if not isinstance(obj, list):
                raise EvidenceError("invalid paginated evidence")
            items.extend(obj)
            if total is not None and len(items) > total:
                raise EvidenceError("collection exceeds declared count")
            following = []
            for part in headers.get("link", "").split(","):
                if 'rel="next"' in part:
                    match = re.fullmatch(r'\s*<([^>]+)>;\s*rel="next"\s*', part)
                    if not match:
                        raise EvidenceError("invalid next link")
                    following.append(match.group(1))
            if not following:
                if total is not None and len(items) != total:
                    raise EvidenceError("pagination incomplete")
                return items
            if len(following) != 1:
                raise EvidenceError("ambiguous pagination")
            parsed = urlsplit(following[0])
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise EvidenceError("cross-origin pagination")
            if collection is not None:
                params = parse_qsl(parsed.query, keep_blank_values=True)
                pages = [value for key, value in params if key == "page"]
                if (parsed.path != origin.path or parsed.fragment
                        or sorted((key, value) for key, value in params if key != "page") != filters
                        or len(pages) != 1 or pages[0] != str(len(seen) + 1)):
                    raise EvidenceError("pagination changed collection scope")
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
        raise EvidenceError("pagination incomplete")


def verify_pr_snapshot(api, pr_number: int, expected_head: str, expected_main: str):
    if type(pr_number) is not int or pr_number < 1:
        raise EvidenceError("invalid PR")
    sha(expected_head)
    sha(expected_main)
    pr, _ = api.get(f"/repos/{REPO}/pulls/{pr_number}")
    main, _ = api.get(f"/repos/{REPO}/git/ref/heads/main")
    if not isinstance(pr, dict) or not isinstance(main, dict):
        raise EvidenceError("invalid PR provenance")
    if (not isinstance(pr.get("head"), dict) or
            not isinstance(pr.get("base"), dict) or
            not isinstance(pr["head"].get("repo"), dict) or
            not isinstance(pr["base"].get("repo"), dict) or
            pr["head"]["repo"].get("full_name") != REPO or
            pr["base"]["repo"].get("full_name") != REPO):
        raise EvidenceError("PR repository provenance mismatch")
    if (pr.get("number") != pr_number or pr.get("state") != "open" or
            pr.get("draft") is not True or pr.get("merged_at") is not None or
            pr.get("head", {}).get("sha") != expected_head or
            pr.get("base", {}).get("sha") != expected_main or
            pr.get("base", {}).get("ref") != "main" or
            main.get("object", {}).get("sha") != expected_main):
        raise EvidenceError("PR/head/base mismatch")
    return {"state": "read_only_verified_snapshot", "repository": REPO,
            "pr_number": pr_number, "head_sha": expected_head, "main_sha": expected_main}


def _positive(value):
    return type(value) is int and value > 0


def _same_repository(value):
    # Stable GitHub repository ID plus its canonical owner/name.
    return (isinstance(value, dict) and type(value.get("id")) is int
            and value["id"] == 1457187422 and value.get("full_name") == REPO)


def verify_workflow_provenance(api, pr_number, expected_head, expected_main, rules):
    """Evaluate exact-head read-only evidence, not approval or launch authority."""
    verify_pr_snapshot(api, pr_number, expected_head, expected_main)
    if (not isinstance(rules, list) or not rules
            or any(not isinstance(r, WorkflowRule) for r in rules)
            or len({r.path for r in rules}) != len(rules)
            or len({r.name for r in rules}) != len(rules)):
        raise EvidenceError("invalid workflow policy")
    runs = api.pages(f"/repos/{REPO}/actions/runs?event=pull_request"
                     f"&head_sha={expected_head}&per_page=100", collection="workflow_runs")
    result = {}
    for rule in rules:
        path = safe_path(rule.path)
        main_file, _ = api.get(f"/repos/{REPO}/contents/{path}?ref={expected_main}")
        head_file, _ = api.get(f"/repos/{REPO}/contents/{path}?ref={expected_head}")
        if (not isinstance(main_file, dict) or not isinstance(head_file, dict)
                or main_file.get("sha") != rule.trusted_blob_sha
                or head_file.get("sha") != rule.trusted_blob_sha
                or main_file.get("type") != "file" or head_file.get("type") != "file"):
            raise EvidenceError("workflow changed or unverified")
        matched, identifiers, numbers = [], {}, {}
        seen = set()
        workflow_id = None
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("name"), str) or not isinstance(run.get("path"), str):
                raise EvidenceError("invalid workflow run evidence")
            if run["name"] != rule.name and run["path"] != path:
                continue
            if (run["name"] != rule.name or run["path"] != path
                    or run.get("event") != "pull_request" or run.get("head_sha") != expected_head
                    or not _same_repository(run.get("repository"))
                    or not _same_repository(run.get("head_repository"))
                    or not all(_positive(run.get(key))
                               for key in ("id", "workflow_id", "run_number", "run_attempt"))
                    or not isinstance(run.get("status"), str) or "conclusion" not in run
                    or (run["conclusion"] is not None and not isinstance(run["conclusion"], str))):
                raise EvidenceError("invalid exact workflow run provenance")
            linked = run.get("pull_requests")
            if not isinstance(linked, list):
                raise EvidenceError("CI association missing")
            exact = [p for p in linked if isinstance(p, dict)
                     and type(p.get("number")) is int and p["number"] == pr_number]
            if (len(exact) != 1 or not isinstance(exact[0].get("head"), dict)
                    or exact[0]["head"].get("sha") != expected_head):
                raise EvidenceError("CI association mismatch")
            ident, ordinal, attempt = run["id"], run["run_number"], run["run_attempt"]
            if ((workflow_id is not None and workflow_id != run["workflow_id"])
                    or identifiers.setdefault(ident, ordinal) != ordinal
                    or numbers.setdefault(ordinal, ident) != ident
                    or (ident, attempt) in seen):
                raise EvidenceError("ambiguous workflow recency")
            workflow_id = run["workflow_id"]
            identifiers[ident] = ordinal
            numbers[ordinal] = ident
            seen.add((ident, attempt))
            matched.append(run)
        if not matched:
            raise EvidenceError("trusted exact-head CI missing")
        definition, _ = api.get(f"/repos/{REPO}/actions/workflows/{workflow_id}")
        if (not isinstance(definition, dict) or not _positive(definition.get("id"))
                or definition["id"] != workflow_id or definition.get("path") != path
                or definition.get("name") != rule.name):
            raise EvidenceError("workflow definition provenance mismatch")
        # GitHub increments run_number for new runs of a workflow and
        # run_attempt for reruns of that same run. API response order is not evidence.
        current = max(matched, key=lambda run: (run["run_number"], run["run_attempt"]))
        if current["status"] != "completed" or current["conclusion"] != "success":
            raise EvidenceError("latest exact workflow run did not succeed")
        result[rule.name] = {"path": path, "trusted_blob_sha": rule.trusted_blob_sha,
                             "run_ids": [current["id"]], "run_attempt": current["run_attempt"]}
    verify_pr_snapshot(api, pr_number, expected_head, expected_main)
    return {"state": "ci_provenance_verified", "head_sha": expected_head,
            "checks": result, "execution_authority": False}


def dispatch(*args, **kwargs):
    raise RuntimeError("DISPATCH DISABLED")

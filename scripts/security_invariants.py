"""Non-networked safety invariants for development CI.

This is a narrowly scoped tripwire, not a full vulnerability/secret scanner.
It checks the dangerous default-on regressions in the paid render path.
"""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_provider_allowlist():
    tree = ast.parse((BACKEND / "provider_config.py").read_text())
    matches = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(x, ast.Name) and x.id == "SUPPORTED_PROVIDERS"
                for x in node.targets)
    ]
    require(len(matches) == 1, "Expected one static provider allowlist")
    require(ast.literal_eval(matches[0].value) == {"disabled"},
            "Paid providers must remain disabled in application allowlist")


def check_worker_readiness():
    tree = ast.parse((BACKEND / "worker_readiness.py").read_text())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "worker_readiness"), None)
    require(fn is not None, "Missing worker readiness function")
    assignments = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and
                           t.id == "durable_cost_enforcement"
                           for t in n.targets)]
    require(len(assignments) == 1 and
            isinstance(assignments[0].value, ast.Constant) and
            assignments[0].value.value is False,
            "Do not claim durable-cost readiness without reviewed financial wiring")


def check_runtime_guard():
    source = (BACKEND / "render_pipeline.py").read_text()
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "create_render_pipeline"), None)
    require(fn is not None, "Missing paid-render entrypoint")
    calls = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    require("validate_provider_config" in calls,
            "Provider allowlist check removed from render entrypoint")
    require("require_worker_readiness" in calls,
            "Durable worker readiness check removed from render entrypoint")


def check_no_obvious_inline_credentials():
    # Deliberately scan tracked Python and IaC sources, not test fixtures.
    patterns = [
        re.compile(r'(?i)(?:fal[_-]?(?:key|api[_-]?key)|client[_-]?secret)'
                   r'\s*[:=]\s*["\\\'][A-Za-z0-9_\\-]{20,}["\\\']'),
        re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    ]
    paths = [*BACKEND.glob("*.py"), *(ROOT / "infra").rglob("*.bicep")]
    for path in paths:
        data = path.read_text()
        for pattern in patterns:
            require(not pattern.search(data),
                    f"Potential hard-coded secret in {path.relative_to(ROOT)}")


def main():
    check_provider_allowlist()
    check_worker_readiness()
    check_runtime_guard()
    check_no_obvious_inline_credentials()
    print("Paid-execution invariant checks passed (not a complete security audit)")


if __name__ == "__main__":
    main()

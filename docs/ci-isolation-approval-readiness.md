# Isolated GitHub Actions: security-agent review and approval-removal gate

**State:** Development-only draft. Keep Copilot cloud agent's **Require approval for workflow runs** ON. This change does not merge, deploy, alter IAM/finance, or enable Copilot dispatch.

## Threat model

A same-repository Copilot PR can change its own Actions YAML. If routine workflow approvals are disabled while privileged repository-wide secrets remain accessible to PR workflows, an attacker can exfiltrate those secrets *before a CI scan blocks merging*. Static checks alone do not solve this problem.

## Implemented changes

- PR backend, security, audit, policy and synthetic CI: read-only repository access and checkout credentials not persisted.
- Retire legacy frontend auth self-commit/push with `contents: write`; replace with manual main-only, locked read-only build.
- Retire legacy feature-branch-triggered Stage B Azure OIDC workflow; allow only manual main-only audit with existing environment.
- Guard manually dispatched Azure preview and Azure OIDC verification on main. Preserve existing environments and all credential restrictions.
- A trusted `pull_request_target` **data-only** scanner reads candidate workflow YAML from an inert checkout; it runs its Python code from protected main with `contents: read`, no secrets, no OIDC and no environment. It never executes candidate code.
- Deterministic deny tests cover secret access, elevated PR tokens, credential persistence, general-purpose `pull_request_target`, unguarded OIDC and unguarded privileged dispatch.

## Mandatory checks before the owner may switch approvals OFF

1. Independent security **agent** reviews the exact PR head and issues an explicit verdict. The deterministic policy scan does not count as an independent agent review. Any change to the scanner or trusted gate requires sensitive review and may not self-approve.
2. Move `COPILOT_DISPATCH_TOKEN`, `AUTO_MERGE_USER_TOKEN` (if configured), Azure credentials and paid-provider keys **out of repository-wide Actions secrets** into main-only, branch-restricted GitHub environment secrets or an equivalent separately secured service. **Separate owner authorization required** to migrate or remove secrets; none occurs in this PR.
3. Independently verify GitHub environment deployment-branch restrictions to `main`, GitHub OIDC subjects, and manual gates for Azure/IAM/spending operations. The management controller may use a distinct main-only restricted environment without recurring approval only after review.
4. Require the actual `Trusted workflow isolation (read-only)` check on protected main after the workflow is merged and tested. Retain required Backend/Security tests and independently reviewed Copilot code review. Do not treat workflow runs whose head changed as valid evidence.
5. Run negative-path validation: a harmless PR proceeds in isolated CI; a PR attempting a secret reference, write token or unsafe workflow change is rejected. Confirm no PR job can access a credential before that check runs.
6. Only after all checks pass should the owner separately authorize switching the Copilot workflow approval policy OFF. Leave all privileged workflow environment approvals in place.

## Remaining risk and rollback

A trusted static scanner verifies YAML properties; it cannot establish the complete GitHub secret access policy, prevent all unsafe code execution, or guarantee supply-chain safety. A future PR changing YAML and bypassing scanner logic remains a sensitive change. If leakage or privilege drift is suspected, keep/restore workflow approvals, halt affected jobs, rotate exposed credentials and remediate through protected main.

Preserve maximum two coding agents, included Copilot credits only and zero paid overages, protected main, no Azure deployment or write RBAC, paid FAL disabled, and no automatic merge or automatic dispatch in this phase.

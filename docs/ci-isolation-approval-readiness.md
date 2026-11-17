# Isolated GitHub Actions: security-agent review and approval-removal gate

**State:** Development-only draft. Keep Copilot cloud agent's **Require approval for workflow runs** ON. This PR now prepares dedicated controller Environment-secret references but does **not** create GitHub Environments, move/delete credentials, merge, deploy, alter IAM/finance, or enable Copilot dispatch.

## Threat model

A same-repository Copilot PR can change its own Actions YAML. If routine workflow approvals are disabled while privileged repository-wide secrets remain accessible to PR workflows, an attacker can exfiltrate those secrets *before a CI scan blocks merging*. Static checks alone do not solve this problem.

## Implemented changes

- PR backend, security, audit, policy and synthetic CI: read-only repository access and checkout credentials not persisted.
- Retire legacy frontend auth self-commit/push with `contents: write`; replace with manual main-only, locked read-only build.
- Retire legacy feature-branch-triggered Stage B Azure OIDC workflow; allow only manual main-only audit with existing environment.
- Guard manually dispatched Azure preview and Azure OIDC verification on main. Preserve existing environments and all credential restrictions.
- A trusted `pull_request_target` **data-only** scanner reads candidate workflow YAML from an inert checkout; it runs its Python code from protected main with `contents: read`, no secrets, no OIDC and no environment. It never executes candidate code.
- Deterministic deny tests cover secret access, elevated PR tokens, credential persistence, general-purpose `pull_request_target`, unguarded OIDC and unguarded privileged dispatch.
- Nov 17 hardening: trigger allowlist (unknown events denied); `workflow_call` callees treated as caller (possibly PR) context; `secrets: inherit` and unpinned remote reusable workflows denied; privileged and default-branch-context (`workflow_run`, `issues`, `schedule`) jobs may only check out `main` or a literal reviewed commit SHA; attacker-controlled event text (`github.event.*` titles/bodies/branch names, `github.head_ref`, whole-`github` dumps) in `run:`/`github-script` denied; YAML anchors, merge keys, duplicate keys, multi-document files, symlinks and case-variant suffixes denied; any scanner error or missing parser exits non-zero; the gate's PyYAML install is hash-pinned (`scripts/ci-isolation-requirements.txt`). Security Checks runs the regression suite with the real parser and fails on any skipped test.

## Repository protection gaps found Nov 17 (owner settings; not changed by this PR)

- `main` ruleset requires a PR but **0 approving reviews**, no code-owner review, no stale-review dismissal and no last-push approval, so GitHub does not enforce an independent approval or invalidate it when the head changes. Raise to ≥1 approval with *dismiss stale approvals* and *require approval of the most recent push* before relying on review as a control.
- Add a `CODEOWNERS` entry for `.github/` and `scripts/check_ci_isolation.py` with *require code-owner review*: the trusted gate runs main's scanner, so a PR that weakens the scanner itself is only caught by human review.
- After merge, add `Trusted workflow isolation (read-only)` to the required status checks (it cannot be required before it exists on `main`, because `pull_request_target` always runs the base-branch copy).

## Mandatory checks before the owner may switch approvals OFF

1. Independent security **agent** reviews the exact PR head and issues an explicit verdict. The deterministic policy scan does not count as an independent agent review. Any change to the scanner or trusted gate requires sensitive review and may not self-approve.
2. Create a **main-branch-only** `design-management-control` Environment, store a replacement token under the new Environment secret name `DM_COPILOT_DISPATCH_TOKEN`, and only if later authorized store `DM_AUTO_MERGE_USER_TOKEN`. Remove corresponding legacy repository-wide secrets after Environment storage is confirmed. Inspect all remaining repository secrets and migrate/remove privileged provider or cloud tokens separately (do not delete unknown active credentials blindly). Environment settings and secret values require the owner's GitHub administrative access; this PR changes code references only.
3. Independently verify GitHub environment deployment-branch restrictions to `main`, GitHub OIDC subjects, and manual gates for Azure/IAM/spending operations. The management controller may use a distinct main-only restricted environment without recurring approval only after review.
4. Require the actual `Trusted workflow isolation (read-only)` check on protected main after the workflow is merged and tested. Retain required Backend/Security tests and independently reviewed Copilot code review. Do not treat workflow runs whose head changed as valid evidence.
5. Run negative-path validation: a harmless PR proceeds in isolated CI; a PR attempting a secret reference, write token or unsafe workflow change is rejected. Confirm no PR job can access a credential before that check runs.
6. Only after all checks pass should the owner separately authorize switching the Copilot workflow approval policy OFF. Leave all privileged workflow environment approvals in place.

## Remaining risk and rollback

A trusted static scanner verifies YAML properties; it cannot establish the complete GitHub secret access policy, prevent all unsafe code execution, or guarantee supply-chain safety. A future PR changing YAML and bypassing scanner logic remains a sensitive change. If leakage or privilege drift is suspected, keep/restore workflow approvals, halt affected jobs, rotate exposed credentials and remediate through protected main.

Preserve maximum two coding agents, included Copilot credits only and zero paid overages, protected main, no Azure deployment or write RBAC, paid FAL disabled, and no automatic merge or automatic dispatch in this phase.

## One-time secret isolation operator procedure

**Before attempting these steps:** The repository is private and personally owned. GitHub Environment secrets and selected deployment branches for private repositories require an eligible GitHub subscription (GitHub Pro, Team or Enterprise); a Copilot Pro subscription by itself does not establish that eligibility. If the Environment secret controls are unavailable, keep Copilot workflow approvals ON and choose an equivalent separately secured GitHub App/service broker instead; do not make the repository public or incur paid upgrades without owner permission.

1. Navigate to **Repository → Settings → Environments → New environment**, and name it **`design-management-control`**. Under Deployment branches/tags choose **Selected branches and tags**, add only **Branch: `main`**, with no tags. Verify that the protected main ruleset still applies. The controller job uses `environment: design-management-control` and `deployment: false`; this is credential isolation, not Azure deployment.
2. Under that Environment's **Environment secrets**, create `DM_COPILOT_DISPATCH_TOKEN` using a newly rotated, short-lived, least-privilege fine-grained GitHub token restricted to this repository and approved issue-assignment operations. Never paste token values in a chat or issue. You cannot retrieve a prior secret's plaintext from GitHub Settings, so issue a fresh token if its value was not securely retained.
3. **Do not create `DM_AUTO_MERGE_USER_TOKEN` now**. Keep automatic merging disabled; only create it in the Environment after a separately reviewed daily-integration design authorizes its limited use. The code refers to a unique Environment-only name, so missing credentials do not fall back to the previous `AUTO_MERGE_USER_TOKEN` name.
4. In **Settings → Secrets and variables → Actions → Repository secrets**, after confirming the new Environment secret exists, **delete `COPILOT_DISPATCH_TOKEN` and any `AUTO_MERGE_USER_TOKEN`** from *repository-level* secrets. Where broad repository-level paid-provider credentials or Azure bearer credentials exist, plan their removal/rotation into appropriate explicitly gated environments or Azure Key Vault. Do not change identity grants or activate providers.
5. For legacy `stage-b-audit`, verify its existing `stage-b-audit` Environment allows **Branch: `main` only**; configure non-sensitive Azure tenant, subscription and client **identifiers** through `vars.AZURE_CLIENT_ID`, `vars.AZURE_TENANT_ID`, and `vars.AZURE_SUBSCRIPTION_ID`. The audit stays manual and main-only; it does not request Azure write permission.
6. Inventory the remaining **repository-level secret names** without exposing their values. An unexpected repository-level PAT, provider key, Azure client *secret*, signing key or broad automation token is a blocker to removing Copilot workflow approvals. The automatically generated `GITHUB_TOKEN` is different: GitHub creates it per job, and PR jobs must have read-only permissions. **Never store Environment credentials in the `copilot` Environment** used to configure the coding agent.
7. Only after a separate security agent verifies Environment branch restrictions, absence of dangerous repository secrets, and the exact-head CI and review verdict, set `CONTROLLER_ENV_ISOLATION_VERIFIED=true` as an admin-controlled repository Actions variable. This variable is intentionally absent/false by default; enabling it alone does not activate the controller, which also requires `AUTONOMOUS_ENGINEERING_ENABLED=true`. Do not turn either on as part of this migration.

### Verification evidence

Security review must record the GitHub Environment's branch policy (names/settings only), the list of **remaining repository secret names** (not values), the enabled/disabled controller toggles, the current PR head, and the results of the trusted PR isolation check. After the PR is merged and before controller activation, test that a PR run cannot access the Environment, that a manual run from a non-main branch is rejected, and that the main-only controller remains skipped while disabled. Do not emit credential values into logs.

**Rollback:** Keep both approval and controller switches OFF/ON respectively as appropriate: Copilot workflow approvals **ON**, controller dispatch/auto-merge **OFF**. Correct Environment policies rather than restoring a powerful repository-wide secret. Credential rotation is preferable to reusing any potentially exposed PAT.

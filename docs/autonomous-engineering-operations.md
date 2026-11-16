# Autonomous Engineering — activation checklist (draft, OFF by default)

This phase supplies **control-plane code only**; no paid Copilot service, auto-merge, or production action is activated by adding these files. It extends the Design Management baseline on the feature/design-management-auditor branch. Do not merge this stacked PR directly to main before PR #17 is reviewed and integrated.

## Existing repository verification at design time

- PR #17 latest inspected head `dcf5fb63742a72f7a31dd1ffe4bde71c16104b7d` passed Backend Python Tests, Security Checks, Design Management Regression Tests, and Synthetic 300-second MP4 Acceptance.
- Repo `allow_auto_merge=false` as inspected. Enabling the GitHub repository setting is a separate owner action.
- GitHub connection could not inspect protected-main rules via branch-protection API (403); ruleset enforcement therefore cannot be independently proven through the connector.
- Two-administrator spending approvals and separate Azure / paid-provider approvals stay unchanged.

## Deliberate safety boundaries

1. `AUTONOMOUS_ENGINEERING_ENABLED` defaults to false/missing; the entire scheduled/event-driven controller is skipped.
2. `COPILOT_DISPATCH_ENABLED` defaults to false/missing. Copilot can require a paid plan/credits. Do **not** set it true without owner authorization for usage and a spending limit.
3. `COPILOT_REPAIR_ENABLED` defaults to false/missing. Once separately approved, the controller may send at most two `@copilot` repair requests per PR when required exact-head CI fails. It waits for completed CI and never retries a non-idempotent comment on a network timeout. When exhausted, it creates an owner-decision issue only when decision inbox is also enabled. This can consume additional Copilot credits and requires explicit approval.\n4. `AUTO_MERGE_ENABLED` defaults to false/missing.
5. `PROTECTED_MAIN_VERIFIED` defaults to false/missing and must be set only after a manual verification that both `Backend tests (Python 3.12)` and `Security checks (Python 3.12)` are required on main, PRs are required, and bypasses are not allowed. Merely having workflows is not enough.
6. `DECISION_INBOX_ENABLED` defaults to false/missing.
7. Neither workflow nor script uses `--admin`, direct merge endpoints, deployment commands, paid FAL APIs, or bypass mechanisms.

## Tokens

- `COPILOT_DISPATCH_TOKEN`: **user-authorized**, fine-grained user-to-server PAT/OAuth token with minimum issue assignment permission and support for Copilot issue assignment API. Normal `GITHUB_TOKEN` is an installation token and is **not** supported for Copilot agent-task API actions.
- `AUTO_MERGE_USER_TOKEN`: separately scoped token authorized to configure PR auto-merge; GitHub rules still apply.
- Store tokens only as GitHub Actions secrets, never in repository files or issue bodies.
- Do not create either token or enable the toggles without separate explicit authorization.
- Prefer expiring tokens, regular rotation, and distinct credentials for dispatch and merge. Restrict workflow edit permissions.

## Agent dispatch

- Runs hourly, on issue open/close, and when backend/security workflows complete, serialized via concurrency.
- Considers only the five owner-approved development tasks from `docs/design-management-tasks.json`, opened as issues by Design Management, and only if dependencies have explicit owner acceptance.
- Rejects infrastructure/Reader-IAM task dispatch automatically.
- Counts active Copilot-assigned issues and open Copilot PRs conservatively. Starts at most two tasks concurrently; count can double-count, reducing throughput but avoiding oversubscription.
- Uses Copilot REST issue assignment API with `copilot-swe-agent[bot]`. This is public preview and may change. Auto-generated PRs remain subject to separate risk gates.
- Successful assignment does **not** imply paid usage is permitted by this pull request; enabling requires separate owner decision.

## Low-risk auto-merge

- Only Copilot-authored, non-draft, same-repo PRs against main.
- Only small `docs/ui/*.md`, `docs/user-guides/*.md` and narrowly scoped frontend CSS changes, with no protected keywords. All executable code, backend, providers, finance, auth, workflow and infra changes fail closed.
- Requires current head SHA, both completed successful Backend Python Tests and Security Checks on that exact SHA, and manually verified repository protection.
- Calls `gh pr merge --auto --squash --match-head-commit <sha>` on a single eligible PR per controller run. Never `--admin`; GitHub must allow auto-merge and enforce required status gates.
- Enables auto-merge only when explicitly opted in. On any failure, the PR remains for owner action.
- New PR changes require fresh assessment; branch protection must enforce exact-head CI.

## Bounded retries and decisions

- Only transient HTTP 429/5xx GETs get retries (up to 3 attempts with short exponential waits). Non-idempotent issue assignment or issue creation is attempted once; API timeouts are not retried automatically because the remote action may have succeeded.
- Critical failure handling is **decision-only**: idempotently create owner-decision GitHub issues keyed by PR number; do not auto-edit or weaken sensitive code to recover. Bounded repair requests (two per PR) are opt-in and may incur Copilot credit usage; exhaustion creates a decision issue, never an unrestricted retry loop.
- Duplicate decision issues are prevented even when previously closed. Incomplete pagination or CI evidence causes failure, not an unsafe merge.

## Prerequisites before actual activation

1. Merge and verify PR #17 first, under current single-owner governance and required checks.
2. Validate this stacked PR's unit, backend and security checks, review all changed automation files, and separately authorize its development-only merge.
3. Confirm your Copilot paid-plan eligibility and repository agent access, and explicitly approve usage cost ceiling before enabling dispatch.
4. Configure the two separately scoped secrets, require protected main checks, and manually enable repository Allow auto-merge if desired.
5. Enable the master variable, then independently enable dispatch and/or protected auto-merge after a dry-run and manual review.
6. Watch the first 3 runs for issue deduplication, PR gating, exact-head validation, costs, and owner-only exception handling.
7. No Azure deployment, finance authority expansion, or paid FAL activation is part of these steps.

## Scope limitation

This is not a fully autonomous fleet until the GitHub Copilot account, tokens and opt-ins have been independently provisioned. GitHub Actions orchestrates invocation, but no software agent runs continuously as a resident process.

## Copilot PR safety gate and decision #20

GitHub Copilot cloud agent normally opens draft PRs. GitHub Actions on agent pushes require explicit approval by default, and Copilot cannot self-mark its PR as ready or self-merge. The prototype **does not** bypass either behavior. Therefore, unattended low-risk integration is not yet proven or activated. Owner decision [#20](https://github.com/ScottMDavistv/azure-text-to-video/issues/20) records the recommended safe path: keep approval until secret-free PR validation and privileged default-branch separation have been reviewed. Turning off workflow approval is a separate security-sensitive choice and is not authorized here.

For rapid trunk integration, also enable GitHub's automatic deletion of merged head branches only after explicit repository-setting approval, and do not keep feature branches alive after merging. Both PR #17 and this dependent PR remain draft.

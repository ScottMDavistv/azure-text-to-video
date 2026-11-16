# Design Management — supervised reporting verification

This is a GitHub-only diagnostic path. It does not require Azure, secrets other than the job-scoped GitHub token, paid APIs, or deployment.

## Scheduled audit
- Workflow: `.github/workflows/design-management-audit.yml` on protected `main`, scheduled 02:00 and 14:00 UTC, or manually via `workflow_dispatch`.
- Successful runs must update GitHub issue #16 with a `### Automated evidence snapshot — <UTC time>` heading.
- The publisher embeds a unique `GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT` execution identity in the report. The post-publication verifier reads issue #16 and fails closed unless the **entire report content within the sole marker pair**, plus the unique execution identity, exactly matches this run's locally generated artifact. Duplicate, out-of-order, or missing markers are rejected. The verifier never writes. Its status must be green, as must the earlier audit step.
- If the dashboard stays stale, inspect the GitHub Actions run history for **Design Management - Twice Daily Audit**. Check whether the schedule fired, the workflow was disabled, regression tests failed, issue-write permissions were denied, a GitHub API read failed, or the publish step stopped.
- Do not mark scheduled reporting healthy merely because a unit test or a PR check passed. A successful scheduled/manual run and updated issue #16 are required evidence.

## Supervised autonomous-controller smoke
- The unit test `tests/design_management/test_supervised_governance_smoke.py` calls all four controller entry modes with all activation flags **false** and verifies each mode stays non-mutating.
- After this PR has passed CI, an owner may separately approve a *manual* controller dry run on `main` with `AUTONOMOUS_ENGINEERING_ENABLED=false`, `COPILOT_DISPATCH_ENABLED=false`, `COPILOT_REPAIR_ENABLED=false`, `AUTO_MERGE_ENABLED=false`, and `DECISION_INBOX_ENABLED=false`. Never set paid-provider or Azure deployment toggles.
- The existing scheduled controller requires `AUTONOMOUS_ENGINEERING_ENABLED=true` and is not activated by this change.
- Do not enable Copilot dispatch, repair, auto-merge, or issue writes based solely on dry-run results. Individual activation decisions and safe token scopes remain separate.

## Acceptance
1. All design-management offline tests pass in CI.
2. Exact PR-head backend/security CI passes.
3. GitHub Actions scheduled/manual audit has a successful post-publication check.
4. Issue #16 contains the snapshot produced by that run.
5. No Azure deployment or billable provider actions occur.

No merge or deployment is implied by this runbook.

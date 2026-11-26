# C1 Microsoft Entra localhost Playwright validation

Development-only validation in PR #60 against protected `main`. PR #46's authentication changes are already in `main`. This suite does not authorize live sign-in, Azure deployment, IAM or finance changes, paid provider calls, CI dispatch, or a merge.

## Tier 1: synthetic browser tests

See [Tier 1 and Tier 2](TIER1-TIER2.md) for the distinction between synthetic and live evidence. From the repository's `frontend/` directory on a trusted development machine, within an approved development run:

```bash
npm ci
# Exact-version, no-save test runner; never use --package-lock=false (lockfile drift).
npm install --no-save --ignore-scripts @playwright/test@1.65.1
node e2e/c1-lockfile-check.mjs
npx playwright install chromium
npx playwright test --config=playwright.config.mjs
C1_UNCONFIGURED=1 npx playwright test --config=playwright.config.mjs
node e2e/c1-mutation-check.mjs
node e2e/run-browser-mutations.mjs
```

These runs use a synthetic public SPA client ID and a fresh Vite server. They intercept external HTTP and WebSocket requests and never sign in to Microsoft. This evidence does not establish WebRTC/UDP egress control. Frontend Quality Gates runs the synthetic and unconfigured suites and both mutation runners at the exact PR head, with a read-only token and no secrets. CI explicitly sets `C1_LIVE=0`. Dependency and browser setup can still require approved downloads; the synthetic browser cases do not require Microsoft Entra access.

The static runner checks seven source mutations and seven coverage decoys without editing the working tree. The browser runner requires a passing unmutated baseline, then checks each mutation with static checks disabled. Its synthetic M5 check verifies that `logoutPopup` is called for the expected account; it does not verify Microsoft session termination. M6 uses a local HTTP/WebSocket sink. M7-S (storage after real token acquisition) still requires separately authorized live evidence.

Normal runs require a fresh server on port 5173 and a clean checkout, including untracked files. The runner rejects hidden Git index changes and binds the test evidence to the actual checkout. Only offline browser mutation runs may use `C1_MUTATION_MODE=1`; such runs cannot certify a live SHA. Keep each mutation in a disposable worktree and leave the tested checkout unchanged.

## Review and release status

At `cf37876675b2c2c077bf3bfdf7ec6c3f71bca16c` against `9ab83354589fe19d06bf2467cec3ee80ed80cf55`, the full-diff independent review passed within its offline scope, with low-severity notes. Frontend, Backend, Security and Trusted Workflow Isolation checks passed on that exact head. The reviewer ran focused unit checks and a partial static mutation run, not the full browser CI suite. These receipts apply only to that commit, not a later revision or a merge commit. Earlier reviews of superseded heads are historical.

The repeat-permission check requires fresh UI completion and no new popup; it does not imply Entra minted a new token rather than MSAL returning a cached token. The cancellation check requires a committed, open Microsoft login document at the exact expected origin before operator cancellation, followed by popup closure, sanitized MSAL cancellation and empty local account state.

**Live C1 is INCOMPLETE. Microsoft provider sign-out is UNVERIFIED.** Local sign-out tests verify cache and active-account clearing, unavailable token acquisition, and signed-out state after reload. They do not establish that the Microsoft SSO session ended. Issue #61 tracks remaining C1 evidence. #51 remains an open historical review-evidence item for merged PR #46; merge ancestry is not review evidence. #82 remains a merge gate for #60 and #74 and requires a real, supervised R11 pass. The most recent supplied run failed despite cleanup passing. Do not treat this document or the offline review as a waiver or authorization to make #60 Ready or merge it.

For a future, separately authorized live run, use the [Tier 2 gates and procedure](TIER1-TIER2.md). Do not use the parked launcher or bypass R11 or the WebRTC egress gate. Share only sanitized results: exact checkout SHA, browser version, UTC time, test names and verdicts, and sanitized MSAL error codes. Never share bearer tokens, cookies, OAuth URLs, decoded JWT claims, HAR files, raw console output, traces, sign-in screenshots, or browser storage state.

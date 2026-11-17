# C1 — Microsoft Entra localhost Playwright validation

Development-only tests stacked on PR #46, tracked in PR #60. Nothing in this suite authorizes Azure deployment, IAM changes, paid FAL, merging, or billable GitHub Actions/Claude dispatch.

## Execution on a trusted development machine

From the repository's `frontend/` directory:

```bash
npm ci
# Temporary local test dependency only. Pin in the lockfile before promoting to required CI.
npm install --no-save --ignore-scripts @playwright/test@1.65.1
npx playwright install chromium
node e2e/c1-mutation-check.mjs
npx playwright test --config=playwright.config.mjs
C1_UNCONFIGURED=1 npx playwright test --config=playwright.config.mjs
```

Offline runs inject a synthetic public SPA client ID (`00000000-0000-4000-8000-000000000000`) into a fresh Vite server, intercept external requests, and never sign in to Microsoft. The unconfigured case runs separately. **Passing these tests is not a C1 live PASS.**

### Supervised live Entra session

Verify your Microsoft Entra SPA application ID, tenant and registered redirect `http://localhost:5173` (SPA type). Set only the public client ID, not a secret:

```bash
C1_LIVE=1 VITE_ENTRA_SPA_CLIENT_ID=<your-public-spa-client-id> \
  npx playwright test --config=playwright.config.mjs --headed --workers=1
```

A fresh web server is mandatory in all modes; a running server on port 5173 must be shut down first. The runner rejects modified tracked files AND untracked files in a normal test run. Commit or remove local changes before evidence collection. For temporary OFFLINE browser mutation builds only, set `C1_MUTATION_MODE=1`; this overrides the clean-tree rule, explicitly invalidates live certification, and is rejected in `C1_LIVE` mode. The Playwright config supplies `VITE_C1_SHA`, and tests verify the browser received the checkout SHA. Browser reports do not record bearer tokens. `page.pause()` requires operator interaction; manually complete login/logout or cancel the correct popup and resume. Tests are intentionally not enabled in GitHub Actions.

The live test checks tenant, token audience/scope, issuer, expiration, active MSAL account, memory-only storage, silent token retrieval, verified logout/empty MSAL cache, and denial of unsafe egress. The cancelled-login test uses a real button click and records the sanitized `user_cancelled` code from an instrumented MSAL instance. Unauthorized external requests are blocked by a browser-context route **before** network delivery, including popups. The approved Microsoft sign-in host allowlist should be rechecked against real Entra redirects during supervised validation. **Still separately test consent denial and cancelled logout** with a supervised test account; document the outcome without tokens or screenshots showing credentials. A successful suite is not a complete C1 matrix until all consent/cancellation requirements have evidence.

## Security evidence and independent review

Record exact checkout SHA, browser/version, UTC time, test commands, each C1 result, and relevant sanitized MSAL error codes. Do **not** share credentials, bearer tokens, cookies, request URLs containing OAuth parameters, decoded JWT claims, HAR files, console dumps, traces, screenshots of Microsoft sign-in, or auth storage state.

The source mutation runner performs twelve intentionally defective source transformations (M1–M12, across `src/entraAuth.ts`, `src/entraAuthCore.ts` and `src/App.tsx`) **in memory**, verifies that each violates a static constraint, and never changes repository files. It is a useful regression guard but **not a substitute for Playwright runtime mutation testing**. Reviewers should independently repeat browser mutation tests on a temporary checkout before certifying suite readiness.

Current independent Claude verdict on original suite at `d32ec67`: REQUEST CHANGES. Retest and independently review the new exact head before changing this verdict. PR #46's C1 gate and review Thread 1 remain open; C2, C3 and #51 still apply. Never self-approve or automatically merge.

## Independent browser mutation sequence

Use a separate disposable clean checkout for each mutation, apply only one deliberate change at a time, and run `C1_MUTATION_MODE=1 npx playwright test --config=playwright.config.mjs` (with no real client ID). Record the exact **behavioral** failed assertion rather than static source checks alone. Revert every change, remove test artifacts, confirm a clean tree, and rerun the baseline. M5 (no-op logout) and some token-storage-on-success mutations cannot be proven by an unauthenticated offline run; mark these **NOT EXECUTED / REQUIRES SUPERVISED LIVE SESSION** rather than claiming detection from static checks. Never invoke paid providers or send real credentials for mutation testing.

## Claude re-review follow-up (2026-11-17)

The independent reviewer found 4/7 browser mutation detections at `e36f2b0`, with escaped M2/M4 and untested M5. Additional branch commits now add browser-level `127.0.0.1` redirect checks, in-browser interaction-required token fallback spies, logoutPopup invocation spies, exact-origin HTTP restrictions including localhost ports, WebSocket blocking via `routeWebSocket`, stricter storage and cookies/IndexedDB checks, and exact-head evidence enforcement. The runner rejects `git ls-files -v` skip-worktree/assume-unchanged flags and requires a caller-supplied exact `C1_EXPECTED_SHA` in LIVE mode. Sanitized begin/test/end evidence includes `browser.version()` and UTC times. Artifacts write to the OS temporary directory, which must be deleted after evidence review.

**These new browser checks are not independently executed or certified.** Claude must rerun both offline suites in a trusted checkout, compare each original mutation against browser assertions with source invariants disabled, and repeat M6-B wrong-port HTTP, M6-C WebSocket, and M7-B opaque token storage checks using a controlled local sink. M7-S post-success token leakage still requires an authorized live account; do not award a synthetic-browser PASS. Independent reviewer must verify the Microsoft domain allowlist on an owner-approved non-billable supervised dry run without leaking authorization URLs or tokens. Live consent-denial and cancelled-logout handling require separate explicit evidence. PR #46 C1 remains OPEN.

If using `C1_MUTATION_MODE=1`, test runs are development-only and are never acceptable as live C1 SHA attestation. Do not change source code and then run with `C1_LIVE=1`.

## Follow-up to Claude review at ad64f2b

Claude's independent browser re-review found 6 pass / 1 fail / 3 skipped on the offline baseline. B-1 was a bare `@azure/msal-browser` specifier inside `page.evaluate`, which the browser could not resolve. The M4 test now obtains the optimized dependency URL from Vite's transformed `/src/entraAuth.ts` and imports the same module instance, preserving `InteractionRequiredAuthError instanceof` identity. It requires the synthetic fallback to invoke 1 popup and 0 redirects. The evidence `dirty=` marker now reflects actual `git status`, not just `C1_MUTATION_MODE`.

**Not yet independently verified at the new exact head:** clean 0-fail offline baseline and M4 mutant fail at the popup/redirect assertion with static checks disabled. Claude must rerun both, and confirm that the optimized MSAL module resolution works with the actual Vite version. The independent review request is on PR #60 and Issue #61. Live C1 remains INCOMPLETE.

## Alignment with PR #46 certified head `6b62c05` (2026-11-17)

PR #46 split the MSAL flow: `src/entraAuth.ts` now wires the real `PublicClientApplication`/`InteractionRequiredAuthError`, and `src/entraAuthCore.ts` holds the configuration, popup flows and the failed-logout fail-safe. This branch merges `6b62c05` (no conflicts; no application file is changed by this branch) and extends the suite to the new structure:

- Static source invariants (`assertSourceInvariants`) and `c1-mutation-check.mjs` read **both** auth files. New invariants: no `storeAuthStateInCookie: true`; redirect and post-logout URIs bound to `getRedirectUri(origin)` with `getOrigin: () => window.location.origin`; the approved tenant constant; popup consent gated on `isInteractionRequired` wired to `instanceof InteractionRequiredAuthError`; and the failed-logout fail-safe (`setActiveAccount(null)` then `clearCache`).
- Static mutations: M1–M7 retargeted to `entraAuthCore.ts`, plus M8 (cookie auth state), M9/M10 (popup on non-interaction errors, core gate and wrapper predicate), M11/M12 (failed-logout fail-safe removal).
- New offline browser tests: **M9 browser** (a non-interaction silent-token error is rethrown with 0 popups and 0 redirects) and **M10 browser** (a failed sign-out popup still clears the active account and the in-memory cache for that account). These run against the real MSAL instance in Chromium, complementing PR #46's mocked unit tests.

**Authored by a Claude implementation session; not independently reviewed or certified.** Live C1 remains OPEN.

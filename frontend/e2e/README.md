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

The source mutation runner performs seven intentionally defective source transformations **in memory**, verifies that each violates a static constraint, and never changes repository files. It is a useful regression guard but **not a substitute for Playwright runtime mutation testing**. Reviewers should independently repeat browser mutation tests on a temporary checkout before certifying suite readiness.

Current independent Claude verdict on original suite at `d32ec67`: REQUEST CHANGES. Retest and independently review the new exact head before changing this verdict. PR #46's C1 gate and review Thread 1 remain open; C2, C3 and #51 still apply. Never self-approve or automatically merge.

## Independent browser mutation sequence

Use a separate disposable clean checkout for each mutation, apply only one deliberate change at a time, and run `C1_MUTATION_MODE=1 npx playwright test --config=playwright.config.mjs` (with no real client ID). Record the exact **behavioral** failed assertion rather than static source checks alone. Revert every change, remove test artifacts, confirm a clean tree, and rerun the baseline. M5 (no-op logout) and some token-storage-on-success mutations cannot be proven by an unauthenticated offline run; mark these **NOT EXECUTED / REQUIRES SUPERVISED LIVE SESSION** rather than claiming detection from static checks. Never invoke paid providers or send real credentials for mutation testing.

# C1 — Microsoft Entra localhost Playwright validation

Development-only tests stacked on PR #46, tracked in PR #60. Nothing in this suite authorizes Azure deployment, IAM changes, paid FAL, merging, or billable GitHub Actions/Claude dispatch.

## Execution on a trusted development machine

From the repository's `frontend/` directory:

```bash
npm ci
# Exact-version, no-save test runner; never use --package-lock=false (lockfile drift).
npm install --no-save --ignore-scripts @playwright/test@1.65.1
node e2e/c1-lockfile-check.mjs        # installed tree must equal package-lock.json
npx playwright install chromium
npx playwright test --config=playwright.config.mjs
C1_UNCONFIGURED=1 npx playwright test --config=playwright.config.mjs
node e2e/c1-mutation-check.mjs        # 7 static mutations + 7 anti-vacuity coverage decoys
node e2e/run-browser-mutations.mjs    # baseline gate + 7 browser mutations, zero-delivery sink
```

Offline runs inject a synthetic public SPA client ID (`00000000-0000-4000-8000-000000000000`) into a fresh Vite server, intercept external requests, and never sign in to Microsoft. The unconfigured case runs separately. **Passing these tests is not a C1 live PASS.**

### Supervised live Entra session

Verify your Microsoft Entra SPA application ID, tenant and registered redirect `http://localhost:5173` (SPA type). Set only the public client ID, not a secret:

```bash
C1_LIVE=1 VITE_ENTRA_SPA_CLIENT_ID=<your-public-spa-client-id> \
  npx playwright test --config=playwright.config.mjs --headed --workers=1
```

A fresh web server is mandatory in all modes; a running server on port 5173 must be shut down first. The runner rejects modified tracked files AND untracked files in a normal test run. Commit or remove local changes before evidence collection. For temporary OFFLINE browser mutation builds only, set `C1_MUTATION_MODE=1`; this overrides the clean-tree rule, explicitly invalidates live certification, and is rejected in `C1_LIVE` mode. The Playwright config supplies `VITE_C1_SHA`, and tests verify the browser received the checkout SHA. Browser reports do not record bearer tokens. `page.pause()` requires operator interaction; manually complete login/logout or cancel the correct popup and resume. Frontend Quality Gates runs the offline suite, unconfigured suite, both mutation runners and the lockfile check at the exact PR head (read-only token, no secrets); live mode is never run in GitHub Actions.

The live test checks tenant, token audience/scope, issuer, expiration, active MSAL account, memory-only storage, silent token retrieval, verified logout/empty MSAL cache, and denial of unsafe egress. The cancelled-login test uses a real button click and records the sanitized `user_cancelled` code from an instrumented MSAL instance. Unauthorized external requests are blocked by a browser-context route **before** network delivery, including popups. The approved Microsoft sign-in host allowlist should be rechecked against real Entra redirects during supervised validation. **Still separately test consent denial and cancelled logout** with a supervised test account; document the outcome without tokens or screenshots showing credentials. A successful suite is not a complete C1 matrix until all consent/cancellation requirements have evidence.

## Security evidence and independent review

Record exact checkout SHA, browser/version, UTC time, test commands, each C1 result, and relevant sanitized MSAL error codes. Do **not** share credentials, bearer tokens, cookies, request URLs containing OAuth parameters, decoded JWT claims, HAR files, console dumps, traces, screenshots of Microsoft sign-in, or auth storage state.

The source mutation runner performs seven intentionally defective source transformations **in memory**, verifies that each violates a static constraint, and never changes repository files. It is a useful regression guard but **not a substitute for Playwright runtime mutation testing**. Reviewers should independently repeat browser mutation tests on a temporary checkout before certifying suite readiness.

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

## Alignment with PR #46 `6b62c05` and consolidation of PR #71 (`52659bc`)

PR #46 moved the MSAL configuration and popup flows into `src/entraAuthCore.ts`; `src/entraAuth.ts` now only injects the real `PublicClientApplication` and `InteractionRequiredAuthError`. This branch merges that certified head and PR #71, and retargets every check:

- `c1-source-policy.mjs` inspects `entraAuth.ts`, `entraAuthCore.ts` and `App.tsx`, and **fails closed** if MSAL runtime imports, `PublicClientApplication` or popup/redirect flow calls appear in any other `src` file, or if the wrapper stops delegating to the core. Seven coverage decoys in `c1-mutation-check.mjs` prove it cannot pass vacuously.
- `c1-mutations.mjs` is the single definition of M1–M7 (anchors in `entraAuthCore.ts`/`App.tsx`, exactly-once anchor, received-value proofs), shared by the static and browser runners.
- `run-browser-mutations.mjs` first requires the unmutated browser-only baseline to pass, verifies static checks are disabled, and for M6 asserts zero HTTP/WebSocket deliveries to a local sink.
- From PR #71: the exact-host HTTPS `login.microsoft.com` allowlist entry and its spoofed-host regression test; the lockfile-respecting CI install. Added here: remote Microsoft hosts are restricted to the default HTTPS port (PR #71 review F-4), diagnostics record `scheme://host:port` (F-3), and `c1-lockfile-check.mjs` enforces F-1 in CI.

**Not independently reviewed yet.** The implementing session must not certify these changes. Live C1 remains INCOMPLETE and requires renewed owner authorization naming the new SHAs. PR #46 SSO-logout issue #76 is tracked separately.

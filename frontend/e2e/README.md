# C1 Microsoft Entra Playwright validation

Development-only browser suite for PR #46. No CI workflow, paid actions, IAM modifications, FAL, Azure deployment, or automatic Claude API execution.

## Offline tests

From frontend:
```sh
npm ci
npm install --no-save @playwright/test
npx playwright install chromium
npx playwright test --config=playwright.config.mjs
```
Leave `VITE_ENTRA_SPA_CLIENT_ID` unset in `.env.local` for the unconfigured-SPA test. The Playwright package is intentionally not committed to the existing npm lockfile or CI: a separately authorized, pinned-lockfile change is necessary before making it mandatory CI.

## Live, supervised browser checks

Confirm correct Entra **SPA** public client ID and register `http://localhost:5173` under SPA redirect URIs. Set `VITE_ENTRA_SPA_CLIENT_ID` in the ignored `frontend/.env.local`; never enter credentials or tokens there.

```sh
C1_LIVE=1 C1_SPA_CONFIGURED=1 npx playwright test --config=playwright.config.mjs --headed --workers=1
```

Complete the real Microsoft login popup and consent, resume the Playwright inspector at each pause. For the cancellation test, cancel the popup before resuming. Record browser/version, date, tested commit, sanitized errors and each checklist result. Actual consent denial and cancelled logout still require separate supervised verification; mark them INCOMPLETE if not exercised. In-memory MSAL cache prevents reuse of saved authentication state.

The suite returns *only boolean token-audience/scope assertions*, not tokens or full claims. Traces, videos and screenshots are disabled, and console redaction guards are best-effort only. Do not attach HAR/network traces, full auth responses, cookies, access tokens, or credential-bearing evidence.

Passing offline tests is **not C1 certification**. When all live evidence exists, request an independent Claude review of the complete C1 checklist and tested SHA. Claude must return PASS / FAIL / INCOMPLETE for each line, explain limitations, and explicitly recommend C1 closure or continued hold. A Claude review has not been automatically invoked by this suite. PR #46 remains draft and unmerged.

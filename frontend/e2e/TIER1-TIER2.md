# C1 authentication testing — Tier 1 and Tier 2

## Tier 1: unattended on frontend pull requests

The existing `Frontend Quality Gates` pull-request workflow runs the committed frontend unit tests, a pinned Playwright browser suite, seven intentionally defective *browser-level* mutations in disposable temporary Git worktrees, lint and the production build. It runs **against the exact PR head SHA**, then restores GitHub's PR merge checkout for lint/build compatibility checks. GitHub Actions job uses read-only permissions and no deployment credentials or Microsoft identity. The job has a 15-minute upper bound and its added browser step an 8-minute limit. It uses standard GitHub-hosted runners and therefore consumes GitHub Actions minutes; any spending limits and availability of included credits remain controlled in GitHub billing settings.

The synthetic browser tests initialize the real `entraAuth.ts` and interact with the real React UI, but replace instance methods of the MSAL client in browser memory. They exercise synthetic login, token scope acquisition, logout, login denial, cancelled login, consent failure after an interaction-required fallback, and cancelled logout. **No real Entra token is issued** and the synthetic tests cannot demonstrate MFA, tenant registration or a real delegated grant.

Browser egress controls abort all external HTTP and WebSocket attempts. Do not introduce tokens, real accounts, persistent MSAL browser state, GitHub secrets or provider API calls into Tier 1.

The workflow may be pending manual approval under repository rules; do not bypass these settings. Preserve its existing `contents: read` permission and `persist-credentials: false` checkout. A run passing is implementation/CI evidence, **not an independent security review**; Claude must review the exact head before approval or merge.

## Tier 2: real Microsoft Entra integration — supervised owner decision

**Current real development registrations** (verify in the portal before testing):
- Frontend SPA public client ID: `6d5e9e96-8be6-445c-9176-3b67d020612c`
- API application client ID: `a5a09b74-aead-455f-9de7-9b844c8a8465`
- Tenant: `773e9051-c546-4b75-9432-90f4f8242660`
- Entra SPA registered redirect: `http://localhost:5173` (also a Codespaces URL, but the test expects localhost).

The owner's previously authorized supervised C1 live test remains scoped to **PR #46 auth commit** `73fe6d2a8aeeef97d34ba23c83fcce5e63f649cc` as embedded in **PR #60 suite head** `6b45d0acad10065cb825c12c4544cabdad3441d7`. Do not confuse the newer **Tier 1 draft PR #69** commit with that already-reviewed C1 execution head; re-review any head change before updating the live target.

### Minimal owner procedure

1. Keep Codespaces ports 5900 and 5901 **unforwarded**; use only privately forwarded, authenticated port **6080** to access the noVNC desktop. Xvfb is display `:99` and noVNC proxies VNC `127.0.0.1:5901`.
2. Use an authorized **dedicated non-admin test account** with no financial/provider privileges; do not place its password, recovery factors, access tokens, cookies or browser profile in the repo, GitHub secrets, ChatGPT, or Claude. Follow tenant MFA/Conditional Access policies; do not disable or circumvent them.
3. Check out a clean worktree at the exact reviewed PR #60 SHA, confirm the current GitHub PR head still matches, and run:
   ```bash
   cd /workspaces/azure-text-to-video/frontend
   export DISPLAY=:99
   export VITE_ENTRA_SPA_CLIENT_ID=6d5e9e96-8be6-445c-9176-3b67d020612c
   export C1_EXPECTED_SHA=6b45d0acad10065cb825c12c4544cabdad3441d7
   C1_LIVE=1 npx playwright test --config=playwright.config.mjs --headed --workers=1 --grep '^LIVE:'
   ```
4. In the private noVNC desktop, complete sign-in and consent using only the verified Microsoft domain. Pause/resume the Playwright Inspector. Perform the separate consent-denial and cancelled-logout checks using an appropriately unconsented test user where tenant policy permits; if tenant consent or app registration changes would be required, STOP and request separate approval.
5. Record only exact SHA, browser/version, UTC time, test outcomes, and sanitized MSAL error codes (never JWTs, tokens, HAR, cookies, auth screenshots or personal account information). Independently send that evidence to Claude for a LIVE C1 verdict. Require Claude to verify registered SPA redirect, delegated `access_as_user`, account cache after login/logout, Microsoft redirect hosts, and all leftover LIVE-ONLY cases.
6. Tear down the temporary browser/VNC session, remove temp test output, and close private forwarded port 6080.

### Should Tier 2 itself become unattended?

Not by replaying personal passwords or disabling MFA. For high-assurance unattended tests, propose a **separate non-production Entra test tenant**, dedicated non-admin synthetic test identity and a separately reviewed identity-provider integration plan that respects Conditional Access/MFA. This is a **future design proposal**, not authorization to create tenants, modify IAM, grant consent, pay for licenses, or bypass MFA. Service-principal client-credentials tests can verify the backend API but are **not** a substitute for a user-interactive SPA login.

## Gates

- PR #69 (Tier 1), PR #60 (C1 suite), and PR #46 (auth) remain DRAFT and unmerged until independently reviewed, exact-head CI validated and owner merge approval granted for security-sensitive changes.
- No Azure deployment, paid FAL calls, IAM/finance changes, automatic dispatch or workflow-approval bypass.

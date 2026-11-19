# C1 authentication testing — Tier 1 and Tier 2

## Tier 1: unattended on frontend pull requests

The existing `Frontend Quality Gates` pull-request workflow runs frontend unit tests, a pinned Playwright browser suite, seven browser mutations in disposable Git worktrees, lint and the production build. The browser step checks out the exact PR head SHA, then restores GitHub's PR merge checkout for lint and build compatibility checks. The job has read-only permissions, a 15-minute limit and a 15-minute browser step limit. The GitHub-hosted runner consumes GitHub Actions minutes.

The synthetic browser tests initialize the real `entraAuth.ts` and interact with the real React UI, but replace instance methods of the MSAL client in browser memory. They exercise synthetic login, token scope acquisition, logout, login denial, cancelled login, consent failure after an interaction-required fallback, and cancelled logout. **No real Entra token is issued** and the synthetic tests cannot demonstrate MFA, tenant registration or a real delegated grant.

Browser egress controls abort all external HTTP and WebSocket attempts. Do not introduce tokens, real accounts, persistent MSAL browser state, GitHub secrets or provider API calls into Tier 1.

The workflow may be pending manual approval under repository rules; do not bypass these settings. Preserve its existing `contents: read` permission and `persist-credentials: false` checkout. A run passing is implementation/CI evidence, **not an independent security review**; Claude must review the exact head before approval or merge.

## Tier 2: real Microsoft Entra integration — supervised owner decision

**Current real development registrations** (verify in the portal before testing):
- Frontend SPA public client ID: `6d5e9e96-8be6-445c-9176-3b67d020612c`
- API application client ID: `a5a09b74-aead-455f-9de7-9b844c8a8465`
- Tenant: `773e9051-c546-4b75-9432-90f4f8242660`
- Entra SPA registered redirect: `http://localhost:5173` (also a Codespaces URL, but the test expects localhost).

PR #46 is merged into `main` (reviewed auth head `6b62c05aad50620f60f4d26ad2821b74b939f920`). For supervised Tier 2, use only the **current independently reviewed exact PR #60 head**, after checking its ancestry, live-suite compatibility and registered redirect. Never reuse an older pinned suite SHA or infer that synthetic Tier 1 evidence certifies live Entra behavior.

### Minimal owner procedure

1. Use the independently reviewed C1 harness for the supervised Codespaces desktop. Verify private, authenticated forwarding for noVNC on port **6080** through the harness before sign-in. Keep X11 and VNC TCP ports unforwarded and use the harness-managed Unix socket for VNC.
2. Use an authorized **dedicated non-admin test account** with no financial/provider privileges; do not place its password, recovery factors, access tokens, cookies or browser profile in the repo, GitHub secrets, ChatGPT, or Claude. Follow tenant MFA/Conditional Access policies; do not disable or circumvent them.
3. Check out a clean worktree at the exact reviewed PR #60 SHA, confirm the current GitHub PR head still matches, and run:
   ```bash
   cd /workspaces/azure-text-to-video/frontend
   export DISPLAY=:99
   export VITE_ENTRA_SPA_CLIENT_ID=6d5e9e96-8be6-445c-9176-3b67d020612c
   # Set this to the complete current PR #60 head after independent exact-head review.
   export C1_EXPECTED_SHA=REPLACE_WITH_REVIEWED_PR60_SHA
   C1_LIVE=1 npx playwright test --config=playwright.config.mjs --headed --workers=1 --grep '^LIVE:'
   ```
4. In the private noVNC desktop, complete sign-in and consent using only the verified Microsoft domain. Pause/resume the Playwright Inspector. Perform the separate consent-denial and cancelled-logout checks using an appropriately unconsented test user where tenant policy permits; if tenant consent or app registration changes would be required, STOP and request separate approval.
5. Record only the exact SHA, browser/version, UTC time, test outcomes and sanitized MSAL error codes. The independent reviewer verifies the registered SPA redirect, delegated `access_as_user`, Microsoft redirect hosts, local MSAL account/cache state and each supervised cancellation outcome. Microsoft provider sign-out stays **INCOMPLETE** pending separate evidence that the provider session ended.
6. Tear down the temporary browser/VNC session, remove temp test output, and close private forwarded port 6080.

### Should Tier 2 itself become unattended?

Not by replaying personal passwords or disabling MFA. For high-assurance unattended tests, propose a **separate non-production Entra test tenant**, dedicated non-admin synthetic test identity and a separately reviewed identity-provider integration plan that respects Conditional Access/MFA. This is a **future design proposal**, not authorization to create tenants, modify IAM, grant consent, pay for licenses, or bypass MFA. Service-principal client-credentials tests can verify the backend API but are **not** a substitute for a user-interactive SPA login.

## Gates

- PR #46 (auth) is merged into protected `main`. PR #60 (including consolidated Tier 1 coverage from PR #69) remains DRAFT until independently reviewed, exact-head CI validated and owner merge approval granted. PR #69 remains open pending separately authorized consolidation closure.
- No Azure deployment, paid FAL calls, IAM/finance changes, automatic dispatch or workflow-approval bypass.

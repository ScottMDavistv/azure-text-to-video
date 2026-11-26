# C1 authentication testing: Tier 1 and Tier 2

## Tier 1: synthetic checks in ordinary PR CI

Frontend Quality Gates runs unit tests, version-pinned Playwright browser checks, static and browser mutations, lint, build, and a Chromium session-race check. It tests the browser suite at the exact PR head, then restores GitHub's PR merge checkout for the later checks. The job and browser step have 15-minute limits. The hosted runner consumes GitHub Actions minutes.

The synthetic tests use the real React UI and MSAL configuration, with fixture behavior for identity-provider responses. They cover synthetic login, delegated scope acquisition, login and consent failure, cancellation, and local logout. They cannot prove MFA, real tenant consent, real delegated tokens, or Microsoft provider sign-out. HTTP/WebSocket checks are not proof of WebRTC/UDP isolation.

Keep `contents: read`, `persist-credentials: false`, and `C1_LIVE=0` in CI. Respect workflow approval settings. The four checks and the independent offline diff review passed at PR #60 `cf37876` against protected `main` `9ab8335`. For scope and evidence limits, see [the C1 README](README.md). Results do not carry forward to a different SHA.

## Tier 2: real Microsoft Entra integration (blocked)

**Do not start Tier 2 now.** Before an owner can authorize it, require:
- An approved #74 harness and a recorded real R11 pass under #82, including private authenticated desktop forwarding and teardown. #74's parked launcher is not a live runner.
- Independent verification of browser-enforced WebRTC/UDP egress controls, with the existing privacy, forwarding and cleanup gates intact.
- A clean checkout at the exact independently reviewed suite commit and a matching reviewed harness pin. Use a commit named in a new owner approval, whether it is on the PR branch or already in protected `main`; do not assume that the latest PR head or a merge commit is approved. Verify the exact SHA before starting.
- Verified development Entra SPA and API registrations, the expected tenant, the public SPA client ID, and the registered `http://localhost:5173` redirect. No client secrets. Use a dedicated non-admin test account with no financial/provider privileges. Follow tenant MFA and Conditional Access. Do not change registration, grant new consent or weaken tenant controls to complete the test.

### Procedure only after those gates and separate owner approval

1. Use the approved harness for the private Codespaces desktop. Verify private authenticated forwarding for noVNC on port 6080. Keep X11 and VNC TCP ports unforwarded and use the harness-managed VNC Unix socket.
2. Verify the clean checkout, the SHA named in the approval, and the pin used by the approved harness. The live Playwright config requires `C1_EXPECTED_SHA` to match the actual checkout. Use only the approved launch procedure, which supplies that SHA and the verified public SPA configuration. Do not use `C1_MUTATION_MODE` for live evidence.
3. In the private desktop, perform the supervised sign-in, consent and cancellation checks. Verify the expected Microsoft login origin before interaction, and stop if unapproved registration or consent changes would be required. Capture separate evidence for consent denial and cancelled logout.
4. Record the exact SHA, browser/version, UTC time, outcomes and sanitized error codes only. Verify the registered SPA redirect, delegated scope, Microsoft redirect hosts and local cache/account state. Keep Microsoft provider sign-out **UNVERIFIED** unless separate evidence establishes termination of the provider session. Never share tokens, credentials, cookies, or browser profiles.
5. Tear down the temporary browser and VNC session, remove temporary test output after review, and verify private port 6080 is closed.

### Future unattended integration

Do not replay personal passwords or disable MFA. A separate non-production Entra tenant, synthetic identity, and reviewed integration plan would require separate design and owner approval. Service-principal tests can cover the API but do not replace interactive SPA sign-in.

## Integration gates

PR #46 is in protected `main`; PR #69's Tier 1 coverage was consolidated into #60 and #69 was closed without merging. #51 and #61 remain open evidence items. #82 still binds #60 and #74; this documentation does not change it. PR #60 stays Draft until the recorded gates are satisfied or the owner explicitly approves a separate scope decision, and any later merge requires separate approval. No Azure deployment, paid FAL, IAM/finance changes, automatic dispatch or workflow-approval bypass.

# C1 validation harness (parked development assets)

This standalone snapshot preserves the #74 harness and diagnostics for future development. It does not certify C1, enable any workflow or deploy anything. It does not include the unmerged #60 application/Entra validation stack, the #74 frontend or WebRTC integration, or the #74 CI changes.

**The launcher is disabled on this branch.** It supports only help. All operational entry points, including the default command, R11-only, preflight, cleanup, purge, selftest and offline-only, exit before importing the harness. No environment variable or mode enables them. Do not bypass the guard or use parked helpers against a live session. A future change must explicitly reintroduce reviewed dependencies and separately authorize live validation. The preserved library, supplemental specs and tests are for development reference and existing inert fixture use only; there is no live runner or CI wiring here.

## Integration left on the original Draft

These #74 changes are not copied onto main:

- `.github/workflows/frontend-quality-gates.yml`
- `frontend/e2e/c1-webrtc-chromium.sh`, `frontend/e2e/c1-webrtc-policy.mjs`, and `frontend/e2e/c1-webrtc-udp-check.mjs`
- `frontend/playwright.config.mjs`

No changes from Draft #60 are included. The archived launcher retains its historical commit pins as provenance, but the hard stop prevents dependency checkout, installation, preflight, browser tests, networking or session state changes through the launcher.

## Latest supervised R11 evidence (2026-11-24 20:56:45 UTC)

- Tested harness: 9adfd4a417f2d3c2f13163359a0cd4f51476ce6a, the historical Draft PR #74 head, not this standalone snapshot. The tested source was stacked on Draft PR #60 at f4f1066af0a0def2b595a14c211341c27733064c (not included).
- **R11 FAIL, command exit 130.** The decoy passed; the real desktop listener was verified loopback-only. The first monitored authenticated probe had no complete result before its deadline. First recorded failure: monitor-startup startup-timeout. The watchdog handoff was not confirmed.
- The sanitized breadcrumb recorded TERM during monitor arming; the sender was not independently established. The record does not prove a privacy breach, HTTP/authentication rejection, or an underlying transport cause.
- **Cleanup PASS**, including post-cleanup verification. This applies to this run, not to the startup privacy verdict. The immediately preceding attempt at the same commit had unverified post-cleanup listener evidence; five bounded read-only listener samples later passed but did not establish the earlier cause.
- No Entra sign-in was performed. Full C1, sustained monitoring and live privacy remain unverified. [Issue #82](https://github.com/ScottMDavistv/azure-text-to-video/issues/82) remains open.

## Follow-up

Treat both Draft branches and the failed live evidence as historical inputs. Confirm #60 and any required #74 frontend or runtime policy changes in separate reviewed work before deciding whether to re-enable anything. Keep the existing fail-closed privacy and timing gates, isolate the first monitored authenticated probe and verify cleanup, then obtain a separately authorized, supervised R11 PASS and a fresh independent review before any live gate changes.

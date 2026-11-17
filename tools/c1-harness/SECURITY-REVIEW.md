# Independent security review handoff — C1 validation harness

**Status: DRAFT — awaiting independent review. The implementing Claude session has not approved and must not approve this change.**

## What to review

The complete diff of this PR against `test/c1-playwright-entra-validation` at `6b45d0acad10065cb825c12c4544cabdad3441d7`. All changes are under `tools/c1-harness/`:

| File | Role |
|---|---|
| `c1-session.sh` | One-command launcher: preflight → dependencies → offline phases → private desktop → supervised phases → evidence → cleanup |
| `lib/c1-lib.sh` | Commit/worktree verification, preflight, dependency install, Xvfb/x11vnc/noVNC startup, session lock, cleanup |
| `lib/c1-evidence.mjs` | In-memory Playwright report reduction, verdicts, sanitizer, review package |
| `supplemental/playwright.supplemental.config.mjs` | LIVE-only config for the two supplemental supervised cases, serving the same approved worktree |
| `supplemental/c1-supervised.spec.mjs` | Supervised **consent denial** and **cancelled logout** tests, added because the approved README says these must be "separately tested" |
| `tests/test_c1_lib.sh`, `tests/c1-evidence.test.mjs` | Regression tests for startup, failure recovery and security restrictions |
| `README.md` | Operator guide |

**No approved test was modified.** `frontend/**`, PR #46's authentication code, the Entra registration, the origin allowlist and the Playwright allowlists are untouched. `tests/test_c1_lib.sh` enforces this by asserting that `git diff --name-only 6b45d0a HEAD` contains only `tools/c1-harness/**`.

## Review focus (from the assignment)

1. **Network exposure.**
   - The only TCP listener is websockify/noVNC on `127.0.0.1:6080`; the launcher aborts if the set of harness listeners differs.
   - Xvfb runs with `-nolisten tcp -nolisten local` and a random `mcookie` in a `0600` Xauthority file.
   - x11vnc uses `-rfbport 0 -unixsock <0600 socket>`. The RFB handshake test asserts it offers only security type 2 (VNC auth).
   - Codespaces port visibility is set and verified as private with `gh`. If `gh` lacks scope, the operator must type `PRIVATE`.
2. **Authentication handling.** The harness never sees credentials. The operator types them into Microsoft pages in the headed Chromium. The supplemental tests wrap the real MSAL instance and export only `{state, code, aadsts}`, matched against strict regexes.
3. **Credential confidentiality.**
   - Trace, video and screenshots are off.
   - `TMPDIR` is set to the `0700` session directory, so Playwright output and Chromium profiles are created there.
   - Raw JSON reports are read in memory, and the session directory is scanned for JWT/Bearer strings before deletion.
   - The sanitizer refuses output containing JWTs, bearer strings, emails/UPNs, GUIDs, URLs with a query or fragment, OAuth parameter names, cookie headers or long opaque strings.
4. **Cleanup.**
   - Every spawned process carries `C1_HARNESS_SESSION=<session dir>`. Cleanup kills the recorded process groups plus every process whose environment carries the marker, including orphaned Vite servers.
   - The session directory is then removed (`realpath`-guarded `rm`). Ports 5173 and 6080 must be free afterwards.
   - A session lock with PID and kernel start time detects stale sessions after a crash.
5. **Exact-head evidence integrity.**
   - The approved SHAs are pinned constants with no environment override.
   - The auth commit must be an ancestor, and `git diff --name-only auth pw` must equal the six approved files exactly.
   - Tests run in a detached worktree at exactly `6b45d0a`, clean, with no skip-worktree or assume-unchanged entries. This is re-checked after dependency installation and again after the run.
   - The approved config's own `VITE_C1_SHA` and `assertBuild` checks still apply.

## Verification performed by the implementing session (not a substitute for review)

These ran in an Anthropic cloud sandbox, not in Codespaces, with no Microsoft network access:

- `tests/test_c1_lib.sh`: **44 passed, 0 failed, 0 skipped**. This includes the desktop integration tests: loopback-only noVNC, RFB handshake offers only VNC auth, no abstract X socket, one-time password file consumed, cleanup, and stale-session recovery.
- `tests/c1-evidence.test.mjs`: **19 passed, 0 failed**. This includes byte-equivalence of the replicated allowlists, `allowedRequest` and `assertMemoryOnly` against the approved spec at `6b45d0a`.
- `c1-session.sh --offline-only` through the real launcher, against the approved worktree at `6b45d0a`:
  - Static mutation 7/7.
  - Offline suite **7 passed / 0 failed** (3 live tests skipped by design).
  - Unconfigured suite **2 passed / 0 failed**.
  - Evidence: R1–R5 PASS, R6–R9 INCOMPLETE (not executed), overall INCOMPLETE. Cleanup PASS.
  - **Caveat:** the Playwright CDN is blocked in the sandbox, so a locally available Chromium 153 was symlinked in place of revision 1255 (Chromium 159). Codespaces downloads the correct build. Re-run the offline baseline there.
- A headed supplemental run on the Xvfb desktop reached the supervised `page.pause()`, with the Playwright Inspector visible and the MSAL popup aimed at the tenant authority. Microsoft itself was unreachable from the sandbox.
- SIGTERM and SIGHUP while waiting for the operator: listeners closed, zero harness processes left, state directory empty.
- **Not executed:** any live Entra sign-in (R6–R9), Codespaces port forwarding and `gh codespace ports` visibility, or GitHub Actions for this PR (see the PR description).

## Defects found and fixed during implementation (for reviewer awareness)

- Under `pipefail`, `c1_marked_pids` returned non-zero, which aborted cleanup. Fixed and covered by a regression test.
- `--git-common-dir` was relative, so a *modified* cached worktree was silently recreated instead of rejected. Fixed with `--path-format=absolute`; the test now asserts rejection.
- SIGPIPE: `tr </dev/urandom | head` killed the password generator under `pipefail`. The same class of bug affected `grep -q` pipelines and could have hidden a dirty-tree or marker match. All were rewritten without early-exit pipelines.
- The test wrapper originally ran cases inside `if (...)`, where bash disables `errexit`. Cases now run with `errexit` and `pipefail` active, and a signal exit fails the test.
- Cancelled logout: MSAL 4.30 `logoutPopup` clears the local cache **before** opening the popup and swallows popup-closed errors. A cancelled logout therefore resolves; it does not reject with `user_cancelled`. The test detects cancellation by whether the popup ever returned to the app origin.

## Open questions and accepted risks for the reviewer to decide

1. **VNC password strength.** RFB VNC auth truncates passwords to 8 characters (about 47 bits from `[A-Za-z0-9]`). The primary control is GitHub-authenticated private port forwarding; VNC auth is defence in depth. Is that acceptable, or should the harness also require noVNC-side TLS or token auth?
2. **Visibility race.** If a user's default port visibility is Public, port 6080 could be briefly public between the listener starting and the visibility check. VNC auth still applies. Should the harness refuse when `gh` cannot verify, instead of falling back to operator confirmation?
3. **Consent-denial acceptance.** PASS requires `access_denied` plus either `AADSTS65004` or no AADSTS code. Tenant-wide admin consent makes the row permanently INCOMPLETE. Confirm these criteria.
4. **Cancelled-logout semantics.** Local sign-out completes even though Microsoft sign-out is cancelled, so the Microsoft SSO session may survive in that browser. The harness discards the context; real users would not. Is this a PR #46 finding to raise?
5. **Duplicated helpers.** The supplemental spec replicates the approved LIVE egress and storage helpers. Equivalence is tested for the allowlists, `allowedRequest` and `assertMemoryOnly`, and by fragment for the guard. Should these move into a shared module in a future PR #60 revision instead?
6. **Install scripts.** `npm ci` runs lifecycle scripts from the approved lockfile, exactly as the approved README does. `@playwright/test` uses `--ignore-scripts`. `sudo apt-get` installs only the listed packages plus `playwright install-deps chromium`.
7. **Terminal output.** Playwright's list reporter writes to the operator's terminal, and it may include MSAL error text. The harness never persists it; terminal scrollback is the operator's responsibility.

## Requested verdict

APPROVE / REQUEST CHANGES for the harness PR, with findings. Keep the PR in **draft** until that verdict. Don't merge automatically, and don't use this harness's evidence to certify C1 without a separate independent review of a supervised session's review package.

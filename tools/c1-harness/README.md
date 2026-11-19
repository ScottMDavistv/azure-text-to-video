# C1 validation harness — operator guide

One command runs the C1 Microsoft Entra localhost validation in GitHub Codespaces. It opens a private browser desktop that you view in your normal web browser, pauses at each point where you have to act, and writes sanitized evidence plus a review package you paste into a separate Claude session. Nothing gets installed on your own computer.

It is for development only. It never deploys, never touches Azure resources or IAM, never calls paid providers, never merges, and **never certifies C1**. An independent reviewer decides.

## Start

In a Codespace on this branch, open a terminal and run:

```bash
bash tools/c1-harness/c1-session.sh
```

The supervised mode runs **only inside a GitHub Codespace**, because that is where the harness can verify port privacy technically. Anywhere else, use `--offline-only`.

Have these ready:
- The **public** SPA client ID (a GUID). It is not a secret, and it isn't saved anywhere.
- A Microsoft test account for the tenant. Step A also needs an account that has **never** consented to the app; you can revoke consent at https://myapps.microsoft.com.

## What happens

1. **Preflight (automatic).** The harness checks protected-main baseline `c46e2e8…` and PR #60 `f4f1066…`. It checks ancestry and the exact 15-path allowlist, then creates a clean, detached worktree at the pinned Playwright SHA. It rejects modified, untracked, skip-worktree or assume-unchanged files in both the worktree and the harness. It checks Vite's Host-header guard, loopback-only resolution for `localhost`, and availability of ports 5173 and 6080. Any failure stops preflight.
2. **Dependencies (automatic, idempotent).** The harness installs missing approved desktop packages, installs frontend dependencies from the approved lockfile when needed, and installs pinned Playwright and Chromium. It starts installer commands with an empty environment and passes through only the named command lookup, home, locale, proxy and CA-trust settings in `C1_INSTALL_ENV_ALLOW`.
3. **Offline checks (automatic).** It runs the static mutation check, the browser-level mutation runner (when the pinned suite has one), the offline synthetic suite and the unconfigured suite. The approved suite runs unmodified, in a minimal allowlisted environment. Your terminal shows one sanitized line per test; raw Playwright output stays in the private session directory.
4. **Private desktop.** The harness starts the X display and VNC server (Unix sockets only, no IPv4 or IPv6 TCP), verifies that nothing it started listens on TCP, then **proves port 6080 is private before serving the desktop**:
   - A decoy serves a random one-time value on `127.0.0.1:6080`, with no desktop behind it.
   - The harness pins the forwarding domain to `app.github.dev` and rejects conflicting environment values before using a token. Desktop components and the decoy run with only the allowlisted desktop environment.
   - An **unauthenticated** request to the Codespaces URL must be **positively denied**: HTTP 401/403, or an HTTPS redirect to `github.com`/a valid GitHub subdomain. The observed `https://github.dev/pf-signin` redirect (optional query) is accepted **only** when `gh` freshly confirms port 6080 is `private` and the probe targets that exact forwarding origin. Other `github.dev` redirects are refused. A request carrying your Codespace's `GITHUB_TOKEN` **must** then reach the decoy.
   - Anything else is **ambiguous** and never counts as private: a reset or dropped connection, a timeout, a DNS or TLS failure, a 5xx, another 4xx, or a redirect anywhere else. The harness keeps retrying for up to 120 s and then refuses to serve the desktop.
   - Organization-wide visibility is excluded through a `gh` read-back of `private`, or because the repository is owned by a user account.
   - Only then does the real noVNC start. The check is repeated before password disclosure and before each supervised step. The monitor waits 0.2 s between sweeps. A sweep allows up to 0.4 s for each HTTPS probe and up to 0.4 s plus 0.1 s kill grace for one `gh` read-back where required. Public visibility, ambiguous HTTPS evidence or an unavailable required `gh` read-back triggers teardown on the first failed sweep.
   - Every stop uses the same verified teardown: it marks the session privacy-failed, sends TERM, waits, sends KILL, and confirms port 6080 is closed. If it cannot confirm that, the session aborts and full cleanup runs.
   - There is **no manual "type PRIVATE" fallback**. If privacy cannot be proven, the desktop is never served and the evidence records a FAIL.
   - When the check passes, the terminal prints a link and a **one-time VNC password**.
5. **Supervised steps.** You act in the desktop. When you're done with a step, click **Resume ▶** in the *Playwright Inspector* window. You can type `s` at any prompt to skip a step; that row becomes INCOMPLETE.
   - **A: Consent denial.** Before the step, confirm that the account has no prior consent. Sign in, click **Cancel** on the permissions prompt, and Resume. Then confirm that the consent prompt appeared and that you cancelled it. R8 passes **only** with MSAL `access_denied` **and** `AADSTS65004` **and** both confirmations; anything less is INCOMPLETE.
   - **B: Approved LIVE suite.**
     - Test 1: sign in, complete MFA and give **initial consent**, then Resume. Complete the sign-out popup and Resume. R6 verifies local MSAL cache clearing. Microsoft provider sign-out stays **INCOMPLETE** pending separate evidence.
     - Test 2: **close** the sign-in popup without signing in, then Resume.
   - **C: Cancelled logout.** Sign in and Resume. When the "Pick an account to sign out" popup appears, **close it** and Resume.
6. **Evidence and cleanup (automatic).** The harness writes `~/c1-evidence/c1-evidence-<UTC>.md` and `~/c1-evidence/c1-review-package-<UTC>.md`. It then stops the desktop and all test processes, closes the listening ports, deletes the session directory and every temporary file (including the probe token files), and **verifies** the cleanup. A watchdog performs the same cleanup if the launcher itself is killed.

Paste the **review package** into a **separate** Claude session for the independent security review. The command exits non-zero if the evidence verdict is FAIL or cleanup could not be verified.

## Other commands

| Command | Purpose |
|---|---|
| `c1-session.sh --offline-only` | Automated checks only; the supervised rows are INCOMPLETE |
| `c1-session.sh preflight` | Verify commits and install dependencies; runs no tests |
| `c1-session.sh cleanup` | Kill and remove any leftover session, for example after a crash |
| `c1-session.sh purge` | `cleanup`, then delete the cached approved worktree |
| `c1-session.sh selftest` | Run the harness regression tests |

## Security model

- **Network exposure.** Two TCP listeners exist during a session, both on loopback:
  - **noVNC on `127.0.0.1:6080`**, started by the harness. Before noVNC starts, only the privacy decoy listens there. Its Codespaces forwarding is verified private (step 4) and monitored.
  - **The Vite dev server on `127.0.0.1:5173`**, started by the approved Playwright configuration (`npm run dev -- --host 127.0.0.1`) for every Playwright phase. Codespaces may auto-forward it, and **the harness does not verify 5173's forwarding visibility**. A forwarded request arrives with `Host: <codespace>-5173.<forwarding domain>`, and Vite's Host-header check rejects it with 403, because the approved `vite.config.ts` binds `127.0.0.1` and sets no `allowedHosts`. Preflight enforces this statically (`vite_host_guard`, required for R1): exactly one `vite.config.*`, bound to `127.0.0.1`, no `allowedHosts`/`--allowed-hosts`/`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` in the approved Vite, Playwright or package configuration, and the approved webServer command. Test phases also run with an environment allowlist that excludes every `VITE_*`/`__VITE_*` override. The 5173 page is the synthetic test app with a fake client ID offline; in supervised phases it is the real SPA, which holds no secrets. Its behaviour behind real Codespaces forwarding is an independent-verification item.
  - Xvfb runs with `-nolisten tcp -nolisten local` and a random X cookie.
  - x11vnc listens only on a `0600` Unix socket and requires VNC password authentication. It runs with `-rfbport 0 -rfbportv6 0 -no6`: `-rfbport 0` alone disables only IPv4, and LibVNCServer would otherwise bind the wildcard IPv6 `[::]:5900` (the ee6ded8 desktop blocker).
  - Before noVNC starts, nothing the session started may listen on TCP at all, on IPv4 or IPv6. After it starts, the set must be exactly `127.0.0.1:6080`. Listeners are attributed by socket inode (`/proc/PID/fd`) to every harness process, including children that dropped the session marker or called `setsid`, and are read from **both** the kernel tables (`/proc/net/tcp`, `/proc/net/tcp6`) and `ss -Hltne`, which must agree.
  - **Missing evidence fails closed.** A failing or garbled `ss`, an unreadable or malformed kernel table, IPv6 present without a readable `tcp6` table, an unreadable fd table or marker of a recorded harness process, or the two sources disagreeing stops the desktop. It is never treated as "no listener". The same rule applies to "port free" checks used by teardown and cleanup verification.
  - Offending listeners are printed as `address:port pid=N comm=NAME ino=N` (never environment or arguments) and recorded in the evidence. R11 can PASS only when port privacy **and** this listener evidence are verified.
- **Port privacy** is proven technically (step 4) and recorded as row R11. If R11 is not PASS, the session can never be "ready for review". The VNC password is printed only after privacy has been proven.
- **Passwords and tokens.**
  - The VNC password is random for each session and is shown only on your terminal. It reaches x11vnc through a `rm:` password file that x11vnc deletes as soon as it reads it.
  - Your Codespace's `GITHUB_TOKEN` is used only for the privacy probe and the repository-owner lookup. It goes to `curl` through a `0600` header file in the private session directory, never on a command line, and is deleted at cleanup. It is not passed to dependency installers or test phases. The evidence sanitizer and the terminal filter both reject GitHub token formats (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`).
- **Microsoft credentials.** You type them only into Microsoft's own pages inside the desktop. MSAL uses memory-only storage, and each test gets a fresh browser context that is discarded afterwards. Tracing, video and screenshots stay off. Chromium profiles and Playwright output live in the `0700` session directory and are deleted at cleanup.
- **Evidence.** The harness reads raw Playwright results in memory only. Evidence contains only allowlisted values: SHAs, versions, UTC times, PASS/FAIL/INCOMPLETE, sanitized MSAL codes, hostnames, booleans and your yes/no confirmations. A sanitizer refuses output containing JWTs, bearer strings, emails/UPNs, GUIDs, URLs with query strings, cookie headers or long opaque strings.
- **Fail-closed verdict.** The overall result is FAIL if any of the following holds:
  - any executed test fails, or any phase exits non-zero (R12);
  - port privacy is unverified (R11);
  - Playwright is not exactly 1.65.1, or the headless or headed Chromium is not exactly 159.0.8154.6, the build Playwright 1.65.1 expects (R10). Both the consistency check and the literal versions are required;
  - any of the 14 expected offline tests of the pinned suite (C1 and Tier1) is FAIL, SKIPPED, absent or unknown (R3). An INCOMPLETE expected test makes R3 INCOMPLETE, never PASS;
  - the production-review status was not recorded (R13).
- **Uncertified production code (R13).** The only production file that differs between the auth baseline and the tested PR #60 head is `frontend/src/App.tsx`. It is listed in `C1_PROD_REVIEW_PENDING` because it has not been independently certified. The evidence records it with its exact blob SHA, and R13 stays INCOMPLETE until a separate security review certifies it. A session can therefore never be "ready for review" while it is pending. Preflight refuses to start if any other non-test file appears in the approved diff without being listed.
  - a mandatory preflight check did not run (R1).
- **Terminal output.** When a phase produced a Playwright report, the terminal shows only its per-test summary, whether it passed or failed. Failing locator and DOM text, such as the app's `Signed in: <display name>`, stays in the private session directory. A phase without a report shows only sanitizer-approved lines. Lines carrying `Signed in:`, display or user names, emails, GUIDs, tokens or URLs with query strings are withheld. Don't paste raw terminal output anywhere; share the review package instead.

## Troubleshooting

- *Port 5173/6080 in use.* Stop your own dev server, or run `c1-session.sh cleanup`.
- *"unexpected TCP listener before noVNC start".* The lines that follow name each listener (`address:port pid comm ino`). A desktop component exposing TCP is a defect to report. The harness never relaxes this check. A `node` or `chrome` process listed as an orphan is a leftover from an earlier phase: run `c1-session.sh cleanup` and start again.
- *"listener evidence unavailable … failing closed".* `ss` or the kernel's IPv4/IPv6 listener tables could not be read consistently. Check that `ss -Hltne` works and that `/proc/net/tcp` (and `/proc/net/tcp6` where IPv6 is enabled) is readable.
- *Cached approved worktree is modified.* Run `c1-session.sh purge`, then start again.
- *"could not prove port 6080 is private and forwarded".* Check that the PORTS tab shows 6080 and that auto-forwarding is on. Set the port's visibility to **Private**, then run the harness again. The harness never continues without proof. A message saying the unauthenticated probe "was not positively denied (HTTP …)" means the forwarding edge answered ambiguously. The harness treats that as unverified and never as private.
- *Session interrupted (Ctrl-C, closed terminal, killed process).* Cleanup runs on exit, and the watchdog attempts cleanup after a killed launcher. Before any new run or preflight, the harness rechecks session directories and preserved PID/listener-inode evidence, even if the active lock was already removed. If a process, listener, malformed record, or unavailable evidence prevents verification, startup **fails closed**; no new session is started. Investigate the retained evidence and retry `c1-session.sh cleanup` only after resolving the cause. Do not delete unresolved records merely to make a run pass.
- *Egress verdict FAIL listing a Microsoft host.* The approved allowlist blocked a host that Entra really used. Report the hostname to the reviewer; the harness never widens the allowlist.

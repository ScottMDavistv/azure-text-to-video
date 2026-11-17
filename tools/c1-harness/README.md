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

1. **Preflight (automatic).** The harness checks the two pinned commits: auth `73fe6d2…` and Playwright `6b45d0a…` (re-pinned to the aligned PR #60 head after its independent review). It checks that the auth commit is an ancestor and that only the approved test files differ between them, then creates a detached, clean worktree at exactly the Playwright SHA. It refuses any modified, untracked, skip-worktree or assume-unchanged files, in the worktree **and** in the harness itself. It also checks that `localhost` resolves only to loopback and that ports 5173 and 6080 are free. **Any failed check stops the run before anything starts.**
2. **Dependencies (automatic, idempotent).** It installs the approved packages (`xvfb x11vnc novnc websockify fluxbox xauth iproute2`) only if any are missing. It runs `npm ci` only when the lockfile changed, installs `@playwright/test@1.65.1` (no-save, as the approved README does), and installs Chromium.
3. **Offline checks (automatic).** It runs the static mutation check, the browser-level mutation runner (when the pinned suite has one), the offline synthetic suite and the unconfigured suite. The approved suite runs unmodified, in a minimal allowlisted environment. Your terminal shows one sanitized line per test; raw Playwright output stays in the private session directory.
4. **Private desktop.** The harness starts the X display and VNC server (Unix sockets only, no TCP), then **proves port 6080 is private before serving the desktop**:
   - A decoy serves a random one-time value on `127.0.0.1:6080`, with no desktop behind it.
   - An **unauthenticated** request to the Codespaces URL must **not** reach it, and a request carrying your Codespace's `GITHUB_TOKEN` **must** reach it.
   - Organization-wide visibility is excluded through a `gh` read-back of `private`, or because the repository is owned by a user account.
   - Only then does the real noVNC start. The check is repeated before the password is shown and before each step, and a background monitor stops the desktop within seconds if the port ever becomes reachable without GitHub sign-in.
   - There is **no manual "type PRIVATE" fallback**. If privacy cannot be proven, the desktop is never served and the evidence records a FAIL.
   - When the check passes, the terminal prints a link and a **one-time VNC password**.
5. **Supervised steps.** You act in the desktop. When you're done with a step, click **Resume ▶** in the *Playwright Inspector* window. You can type `s` at any prompt to skip a step; that row becomes INCOMPLETE.
   - **A: Consent denial.** Before the step, confirm that the account has no prior consent. Sign in, click **Cancel** on the permissions prompt, and Resume. Then confirm that the consent prompt appeared and that you cancelled it. R8 passes **only** with MSAL `access_denied` **and** `AADSTS65004` **and** both confirmations; anything less is INCOMPLETE.
   - **B: Approved LIVE suite.**
     - Test 1: sign in, complete MFA and give **initial consent**, then Resume. When the harness signs out, complete the sign-out popup and Resume.
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

- **Network exposure.**
  - The only TCP listener the harness opens is noVNC on `127.0.0.1:6080`.
  - Before noVNC starts, only the privacy decoy listens there.
  - Xvfb runs with `-nolisten tcp -nolisten local` and a random X cookie.
  - x11vnc listens only on a `0600` Unix socket (`-rfbport 0`) and requires VNC password authentication.
  - The harness verifies that the set of listeners is exactly `127.0.0.1:6080`.
- **Port privacy** is proven technically (step 4) and recorded as row R11. If R11 is not PASS, the session can never be "ready for review".
- **Passwords and tokens.**
  - The VNC password is random for each session and is shown only on your terminal. It reaches x11vnc through a `rm:` password file that x11vnc deletes as soon as it reads it.
  - Your Codespace's `GITHUB_TOKEN` is used only for the privacy probe and the repository-owner lookup. It goes to `curl` through a `0600` header file in the private session directory, never on a command line, and is deleted at cleanup.
- **Microsoft credentials.** You type them only into Microsoft's own pages inside the desktop. MSAL uses memory-only storage, and each test gets a fresh browser context that is discarded afterwards. Tracing, video and screenshots stay off. Chromium profiles and Playwright output live in the `0700` session directory and are deleted at cleanup.
- **Evidence.** The harness reads raw Playwright results in memory only. Evidence contains only allowlisted values: SHAs, versions, UTC times, PASS/FAIL/INCOMPLETE, sanitized MSAL codes, hostnames, booleans and your yes/no confirmations. A sanitizer refuses output containing JWTs, bearer strings, emails/UPNs, GUIDs, URLs with query strings, cookie headers or long opaque strings.
- **Fail-closed verdict.** The overall result is FAIL if any of the following holds:
  - any executed test fails, or any phase exits non-zero (R12);
  - port privacy is unverified (R11);
  - Playwright is not 1.65.1, or the headless or headed Chromium differs from the build Playwright expects (R10);
  - a mandatory preflight check did not run (R1).
- **Terminal output.** Only sanitizer-approved lines are shown. Don't paste raw terminal output anywhere; share the review package instead.

## Troubleshooting

- *Port 5173/6080 in use.* Stop your own dev server, or run `c1-session.sh cleanup`.
- *Cached approved worktree is modified.* Run `c1-session.sh purge`, then start again.
- *"could not prove port 6080 is private and forwarded".* Check that the PORTS tab shows 6080 and that auto-forwarding is on. Set the port's visibility to **Private**, then run the harness again. The harness never continues without proof.
- *Session interrupted (Ctrl-C, closed terminal, killed process).* Cleanup runs on exit, and the watchdog covers a killed launcher. If something is still left over, the next run detects the stale session and cleans it up; you can also run `c1-session.sh cleanup`.
- *Egress verdict FAIL listing a Microsoft host.* The approved allowlist blocked a host that Entra really used. Report the hostname to the reviewer; the harness never widens the allowlist.

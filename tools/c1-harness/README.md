# C1 validation harness — operator guide

One command runs the C1 Microsoft Entra localhost validation in GitHub Codespaces. It opens a private browser desktop that you view in your normal web browser, pauses at each point where you have to act, and writes sanitized evidence plus a review package you paste into a separate Claude session. Nothing gets installed on your own computer.

It is for development only. It never deploys, never touches Azure resources or IAM, never calls paid providers, never merges, and **never certifies C1**. An independent reviewer decides.

## Start

In a Codespace on this branch, open a terminal and run:

```bash
bash tools/c1-harness/c1-session.sh
```

Have these ready:
- The **public** SPA client ID (a GUID). It is not a secret, and it isn't saved anywhere.
- A Microsoft test account for the tenant. Step A also needs an account that has **not** consented to the app yet. You can revoke consent at https://myapps.microsoft.com.

## What happens

1. **Preflight (automatic).** The harness fetches the two pinned commits: auth `73fe6d2…` and Playwright `6b45d0a…`. It checks that the auth commit is an ancestor and that only the six approved test files differ between them. It creates a detached, clean worktree at exactly `6b45d0a…` and refuses any modified, untracked, skip-worktree or assume-unchanged files. It also checks that `localhost` resolves only to loopback and that ports 5173 and 6080 are free.
2. **Dependencies (automatic, idempotent).** It installs the approved packages (`xvfb x11vnc novnc websockify fluxbox xauth iproute2`) only if any are missing. It runs `npm ci` only when the lockfile changed, installs `@playwright/test@1.65.1` (no-save, as the approved README does), and installs Chromium.
3. **Offline checks (automatic).** It runs the static mutation check, the offline synthetic suite and the unconfigured suite. The approved suite runs unmodified.
4. **Private desktop.** The terminal prints a link and a **one-time VNC password**. Open the PORTS tab and confirm that port **6080** shows **Private**. Then click the globe icon, or use the printed link, and enter the password.
5. **Supervised steps.** You act in the desktop. When you're done with a step, click **Resume ▶** in the *Playwright Inspector* window. You can type `s` at any prompt to skip a step; that row becomes INCOMPLETE.
   - **A: Consent denial.** Sign in with the un-consented account, then click **Cancel** on the permissions prompt.
   - **B: Approved LIVE suite.**
     - Test 1: sign in, complete MFA and give **initial consent**, then Resume. When the harness signs out, complete the sign-out popup and Resume.
     - Test 2: **close** the sign-in popup without signing in, then Resume.
   - **C: Cancelled logout.** Sign in and Resume. When the "Pick an account to sign out" popup appears, **close it** and Resume.
6. **Evidence and cleanup (automatic).** The harness writes `~/c1-evidence/c1-evidence-<UTC>.md` and `~/c1-evidence/c1-review-package-<UTC>.md`. It then stops the desktop and all test processes, closes the listening ports, and deletes the session directory and every temporary file.

Paste the **review package** into a **separate** Claude session for the independent security review.

## Other commands

| Command | Purpose |
|---|---|
| `c1-session.sh --offline-only` | Automated checks only; the supervised rows are INCOMPLETE |
| `c1-session.sh preflight` | Verify commits and install dependencies; runs no tests |
| `c1-session.sh cleanup` | Kill and remove any leftover session, for example after a crash |
| `c1-session.sh purge` | `cleanup`, then delete the cached approved worktree |
| `c1-session.sh selftest` | Run the harness regression tests |

## Security model

- **Network exposure.** The only TCP listener the harness opens is noVNC on `127.0.0.1:6080`, reached through Codespaces **private** port forwarding, which requires your GitHub sign-in.
  - Xvfb runs with `-nolisten tcp -nolisten local` and a random X cookie.
  - x11vnc listens only on a `0600` Unix socket (`-rfbport 0`) and requires VNC password authentication.
  - After start-up, the harness verifies that the set of listeners is exactly `127.0.0.1:6080` and aborts otherwise.
- **Port visibility.** The harness sets the port to private with `gh` and verifies it. If `gh` lacks codespace scope, you must confirm in the PORTS tab and type `PRIVATE`.
- **Passwords.** The VNC password is random for each session and is shown only on your terminal. It reaches x11vnc through a `rm:` password file that x11vnc deletes as soon as it reads it. It is never reused or stored.
- **Microsoft credentials.** You type them only into Microsoft's own pages inside the desktop. MSAL uses memory-only storage, and each test gets a fresh browser context that is discarded afterwards. Tracing, video and screenshots stay off. Chromium profiles and Playwright output live in the `0700` session directory and are deleted at cleanup.
- **Evidence.** The harness reads raw Playwright results in memory only. Evidence contains only allowlisted values: SHAs, versions, UTC times, PASS/FAIL/INCOMPLETE, sanitized MSAL codes such as `user_cancelled`, `access_denied` and `AADSTS65004`, hostnames and booleans. A sanitizer refuses to write output containing JWTs, bearer strings, emails/UPNs, GUIDs, URLs with query strings, cookie headers or long opaque strings.
- **Terminal output.** Playwright's live output goes to your terminal only and is not saved. Don't paste raw terminal output anywhere; share the review package instead.

## Troubleshooting

- *Port 5173/6080 in use.* Stop your own dev server, or run `c1-session.sh cleanup`.
- *Cached approved worktree is modified.* Run `c1-session.sh purge`, then start again.
- *Session interrupted (Ctrl-C, closed terminal).* Cleanup runs on exit. If something is left over, the next run detects the stale session and cleans it up automatically.
- *Egress verdict FAIL listing a Microsoft host.* The approved allowlist blocked a host that Entra really used. Report the hostname to the reviewer; the harness never widens the allowlist.

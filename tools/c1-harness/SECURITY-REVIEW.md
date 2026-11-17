# Independent security review handoff — C1 validation harness

**Status: DRAFT. The remediation awaits a NEW independent full-diff review.** Neither the implementing session nor the author of the earlier reviewer patch may review or approve it.

## History

| Head | What | Who |
|---|---|---|
| `19f8851` | Original harness | Implementation session A |
| — | Independent review of `19f8851`: **REQUEST CHANGES** (H-1, H-2, M-1–M-4, L-1–L-5). [PR #74 comment](https://github.com/ScottMDavistv/azure-text-to-video/pull/74#issuecomment-6960978238) | Reviewer session B |
| this branch | Remediation of every finding, plus a new finding, N-1 | **Session B**, now acting as implementer at the owner's request. Session B also wrote the earlier reviewer patch, which was treated as untrusted input and re-verified here. **Session B must therefore not review this.** |

**Reconciliation update:** The harness pins the protected-main baseline `81c352b2c6f7fdae4e94f2ff4e98a7e1c2bdf6c5` (which contains certified PR #46 auth) and exact-head, three-check-green PR #60 `e1873111e2304a7a739e0f35e2ecdca1d43de44d`. The allowed diff is 15 explicit files, including the frontend workflow and logout UI fix. This integration requires its own independent security review before live operation; no prior certification is transferred to the new SHA.

## What changed (by finding)

| ID | Change | Regression evidence (offline) |
|---|---|---|
| H-1 | The shell test runner no longer calls cases in a `\|\|` context (errexit was masked there), and a self-check case must fail. | `test runner enforces every assertion` |
| H-2 | x11vnc drops `-localhost`, which made x11vnc 0.9.16 refuse every Unix-socket client. `tests/rfb_probe.py` performs full RFB authentication over **both** the Unix socket and the noVNC websocket: none-auth refused, wrong password rejected, correct password accepted. | `desktop: …`; the probe fails if `-localhost` is restored |
| M-1 | Evidence fails closed. **R12**: any FAIL test or non-zero phase. **R11**: port privacy must be technically verified. **R10**: Playwright 1.65.1 plus the headless *and* headed Chromium builds expected by `playwright-core/browsers.json`. **R2B**: the browser-mutation runner, when the pinned suite has one. | `M-1a…M-1d` Node tests; a real `--offline-only` run with substituted Chromium 153 reports FAIL |
| M-2 | Fail-closed Codespaces privacy (see "Port privacy design" below). Supervised mode refuses to run outside Codespaces. The typed `PRIVATE` fallback is removed. | 12 offline cases against `tests/mock_forwarder.py`, plus simulated-Codespace end-to-end runs |
| M-3 | Every signal is ownership-verified (`c1_owned_pid`): the session marker, plus the kernel start time for recorded `pid starttime` entries. The PID must be numeric and above 1, and not the caller. Group signals go only to PIDs that are both group and session leader (the `setsid` leader `c1_spawn` created). | Cases for reused PIDs, legacy/garbage lines, unmarked group members, a marked non-leader and a marked job leader in a foreign session |
| M-4 | R8 PASS requires a passing test, MSAL `access_denied`, `AADSTS65004`, and **two operator attestations** (no prior consent; consent prompt shown and cancelled). Otherwise it is INCOMPLETE, never a risk-accepted PASS. The spec makes `access_denied` without AADSTS INCOMPLETE. | `M-4` Node tests |
| L-1 | The harness self-check rejects skip-worktree/assume-unchanged entries and compares `git hash-object` with `HEAD:` for every harness file. | assume-unchanged and skip-worktree cases |
| L-2 | Phase stdout and stderr go to `0700` session files. The terminal shows only `--summarize` (one sanitized line per test) and `--filter` (sanitizer-approved lines; others withheld). | `L-2` Node tests |
| L-3 | A marker-scoped watchdog in its own session tears everything down when the launcher's PID and start time disappear, and treats a zombie as gone. Cleanup is verified on every exit path; a failure exits non-zero. | `watchdog …` cases; real-launcher SIGKILL clean in about 1 s |
| L-4 | Test phases run under `env -i` with an allowlist (`HOME PATH LANG LC_ALL TERM USER DISPLAY XAUTHORITY C1_HARNESS_SESSION`). `PLAYWRIGHT_*`, `PW_*` and `npm_config_*` are unset. Probes ignore proxies and CA overrides. | `test-phase environment is an allowlist` |
| L-5 | The helper-equivalence tests read the SHA from `c1-lib.sh`, and fail rather than skip. | `L-5` Node test |
| **N-1** (new, High) | `preflight()` used `verification && check label`, which is exempt from errexit, so a failed ancestry/diff, worktree, localhost or post-install check was ignored (present since `19f8851`). Each check now returns on failure, the session aborts before starting anything, and R1 FAILS unless all six mandatory check labels are present. | Real run with broken ancestry: exit 1 "preflight failed; nothing was started"; `N-1` Node test; static check |

## Port privacy design (M-2): to be validated by the reviewer in a real private Codespace

1. Xvfb, fluxbox and x11vnc start with no TCP listener.
2. A Python decoy serves a 128-bit nonce on `127.0.0.1:6080`.
3. The harness probes `https://$CODESPACE_NAME-6080.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN/c1-privacy-<nonce>.txt` twice, with `curl --proto =https --noproxy '*'` under `env -i`, using system CAs, and without following redirects:
   - **without credentials**: any 2xx, or any body containing the nonce, means *public*, and the harness stops;
   - **with `X-Github-Token`** (the header comes from a `0600` file): it must return 200 with the nonce.
   It polls for up to 120 s.
4. Organization-wide visibility also requires sign-in, so the probe alone cannot exclude it. The harness therefore requires either a `gh` read-back of `private` or a repository owner type of `User` (GitHub API, read with the token).
5. The decoy stops, and websockify starts on the same port. `/vnc.html` is re-probed the same way, using the `noVNC` signature, before the password is printed and before each supervised phase.
6. A background monitor re-probes every 15 s. It stops noVNC immediately if the port is seen public, or after 3 consecutive unverifiable checks.

**Assumptions the reviewer must confirm in a real Codespace:**
- an unauthenticated request to a private port returns a redirect or 401/403 rather than a 2xx;
- `X-Github-Token: $GITHUB_TOKEN` reaches a private port from inside the Codespace;
- personal Codespaces offer no organization visibility.

If any of these is false, the design fails **closed**: the desktop is not served.

## Verification performed by the implementing session (not a substitute for review)

Anthropic cloud sandbox: Ubuntu 24.04, Node 22.23.4, x11vnc 0.9.16, ShellCheck 0.9.0. **No real Codespace and no Microsoft network.** The Playwright CDN is blocked, so local runs used Chromium **153** (Playwright 1.65.1 expects **159.0.8154.6**). R10 correctly reports those runs as FAIL.

- `tests/test_c1_lib.sh`: all cases pass, including desktop RFB authentication over unix and websocket, privacy against the mock forwarder, watchdog and process-group safety.
- `tests/c1-evidence.test.mjs`: all pass.
- ShellCheck at warning level: clean. `security_invariants.py`: pass.
- Library mutation check (`lib_mutations.py`, session tooling): 5/6 injected defects are detected. "Recorded start time ignored" escapes by design, because the marker check alone already prevents every observable mis-signal and the start time is a redundant layer.
- **Historical pre-reconciliation execution:** The old `6b45d0a` baseline yielded R1, R2, R3, R4, R5 and R12 PASS; R10 FAIL (Chromium 153); R2B INCOMPLETE (no runner). **These results do not validate the newly pinned `e187311` head.**
- Real launcher in a **simulated** Codespace (local TLS mock of the forwarding domain and `api.github.com`, test CA, failing `gh` shim):

  | Scenario | Result |
  |---|---|
  | Public | Desktop never served, no password printed, R11 FAIL, exit 1 |
  | Private | Password printed only after verification; VNC auth correct via noVNC; only `127.0.0.1:6080` listened; R11 PASS |
  | Flipped to public mid-session | noVNC torn down in 14 s, remaining phases blocked, R11 FAIL |
  | Organization-owned repository | Refused |

  In every scenario the token was absent from the terminal and the evidence, and cleanup was complete.
- Interruptions with the desktop served: SIGTERM, SIGHUP and SIGINT each cleaned up fully in 0.5 s, and SIGKILL in 1.0 s via the watchdog.

**Not executed:** a real Codespace, Chromium 159, browser mutations at the pinned SHA (no runner), live Entra (R6–R9), and GitHub Actions for `tools/c1-harness` (no workflow runs these tests).

## Requested verdict

APPROVE / REQUEST CHANGES for the remediation, with findings. Keep the PR in **draft**. Don't use this harness's evidence to certify C1 until all of the following are done:
1. the re-pin to the independently reviewed PR #60;
2. a real-Codespace instrument validation;
3. a separate independent review of a supervised session's review package.

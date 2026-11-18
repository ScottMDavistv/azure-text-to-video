# Independent security review handoff — C1 validation harness

**Status: DRAFT. The consolidated round-2 remediation awaits a NEW independent full-diff review.** No implementing session listed below may review or approve it. This document is the implementer's account: treat it as claims, not evidence. **C1 REMAINS OPEN.**

## History

| Head | What | Who |
|---|---|---|
| `19f8851` | Original harness | Implementation session A |
| — | Independent review of `19f8851`: **REQUEST CHANGES** (H-1, H-2, M-1–M-4, L-1–L-5). [PR #74 comment 6960978238](https://github.com/ScottMDavistv/azure-text-to-video/pull/74#issuecomment-6960978238) | Reviewer session |
| `27ec6d5` | Round-1 remediation, plus N-1 | Session B (`session_01QdenYMweBLYZsb1EvGL4jt`) |
| — | Independent review of `27ec6d5`: **REQUEST CHANGES** (M-2a, N-2, L-6–L-11, I-3–I-5). [PR #74 comment 6963565503](https://github.com/ScottMDavistv/azure-text-to-video/pull/74#issuecomment-6963565503) | Session C (`session_01QdenYKTaRhz1YJ2XhMuDLF`) |
| `8d92366`…`cd33512` | Partial M-2a/N-2 fixes, then a reconciliation with PR #60 `e187311` and re-pin (PR #74 comments 6964141085 and 6964399093) | Another implementing session (commits authored `ScottMDavistv`) |
| this branch | **Consolidated round-2 remediation** (owner-authorized Option 1): integrates the stronger round-2 work from unpushed `55e1c63`/`e657ed2` onto `cd33512` without force-pushing | Session D (`session_01QdenYPt4giWiEjmFEVy9cD`) |

**Reviewer independence:** sessions B and D and the `cd33512` session are implementers and **must not review**. Session C authored no code but proposed these fixes, so a **new** session is required.

## Pins and the production-code difference (owner condition 4)

| Pin | Value | Status |
|---|---|---|
| `C1_AUTH_SHA_APPROVED` | `81c352b2c6f7fdae4e94f2ff4e98a7e1c2bdf6c5`: protected `main` at the PR #42 merge | Contains certified PR #46 `6b62c05` (an ancestor). Unchanged by this round. |
| `C1_PW_SHA_APPROVED` | `e1873111e2304a7a739e0f35e2ecdca1d43de44d`: PR #60 head (draft) | CI green per PR #60 comment 6964398461. **No independent certification of this head** (`79f87cb`'s does not transfer). Unchanged by this round. |
| `C1_AUTH_TO_PW_FILES` | 15 files | **Verified equal** to `git diff --no-renames --name-only 81c352b e187311` (shell test against real history). It was not widened. |

Production (non-test) code in `81c352b..e187311`: **only `frontend/src/App.tsx`**, blob `a0b753b533165bb12bc516478c8036645f2f292a` at `e187311`, from commit `e187311` "fix(auth): report successful local logout without implying server SSO ended":

```diff
-    try { await signOut() }
-    catch { setAuthMessage('Microsoft sign-out failed. Session content cleared.') }
+    try {
+      await signOut()
+      setAuthMessage('Signed out locally. Microsoft SSO session may remain active.')
+    } catch { setAuthMessage('Microsoft sign-out failed. Session content cleared.') }
```

`frontend/src/c1BuildInfo.ts` is the test build-identity helper; it was among the original six approved files. **App.tsx is NOT treated as independently certified.** This is enforced, not only documented:
- `C1_PROD_REVIEW_PENDING='frontend/src/App.tsx'`.
- Preflight (`production_review_status_recorded`, required for R1) aborts if any allowlisted file outside the test/CI-infrastructure pattern is missing from that list. It also aborts if a listed file is outside the diff or unchanged.
- The evidence records `frontend/src/App.tsx@<blob>`, and the new row **R13** stays **INCOMPLETE** while anything is pending, so the harness can never report READY until a separate security review certifies the change and a reviewed commit empties the list.

Context for the reviewer, outside the pinned diff: between certified PR #46 `6b62c05` and the baseline `81c352b`, `main` merged PR #42. That changed `frontend/src/App.tsx` (account-identity tracking, race handling), `SecureStudio.tsx`, `secureJourney.{mjs,d.mts}`, and `vite.config.ts` (a dev proxy `/projects` → `127.0.0.1:8000`). **`entraAuth.ts`, `entraAuthCore.ts` and `package-lock.json` are byte-identical from `6b62c05` through `e187311`** (shell test). Whether that PR #42 production change on protected `main` needs its own C1-scoped review is an owner/reviewer decision. The harness treats `81c352b` as the baseline because the owner's reconciliation chose it.

## Round 2: what changed (by finding)

Reviewer proposals and the `cd33512` implementation were treated as untrusted input and verified. **Retained from `cd33512`:**
- the pins and the 15-file allowlist;
- the exact-origin `HOST_RE`;
- the supplemental spec's guards copied from the pinned `c1-guards.mjs`, with a byte-equivalence test;
- the mock `ambiguous` (unauthenticated 503) mode and its test;
- the "R3 INCOMPLETE" classification.

| ID | Change | Regression evidence (offline) |
|---|---|---|
| **M-2a** | `c1_unauth_denied`: an unauthenticated result is a denial **only** for 401/403, or for a 301/302/303/307/308 whose `Location` is `https://github.com` or a strict `https://*.github.com` host (default port, no userinfo). This replaces `cd33512`'s unvalidated "any 302/303". Every other result → `unverified`: reset/000, timeout, DNS/TLS/refused, 5xx, other 4xx, a redirect elsewhere, http, or a missing Location. It is shared by the decoy check, the noVNC re-check, the gate and the monitor. | Mock modes `flaky`, `ambiguous`, `unauth5xx`, `unauth404`, `hang`, `badredirect`, `lookalike`, `userinfo`, `httpredirect`, `noloc` → unverified; `unauth401`/`unauth403`/`subdomain` → verified; a 30-case classifier table; DNS/refused/TLS probes; monitor teardown on `flaky`; no listener or noVNC after a failed verification; the password is printed only on the verified branch (static) |
| **N-2** | R3: absent, SKIPPED, FAIL **or unknown** verdict → FAIL (`cd33512` counted unknown verdicts as PASS). INCOMPLETE → R3 INCOMPLETE (kept from `cd33512`; never PASS, never READY). PASS only when every expected test is PASS. **Inventory updated to the 14 offline tests of the pinned `e187311` suite** (8 C1 + 6 Tier1); a test checks it against the pinned spec files. | Node: INCOMPLETE → R3 INCOMPLETE, overall INCOMPLETE; FAIL/SKIPPED/absent/no-result/unknown → FAIL; all PASS → READY; inventory ↔ pinned specs |
| **R13** (new) | Owner condition 4; see above | Node and shell: pending → INCOMPLETE; unrecorded → FAIL; malformed meta rejected; an unlisted production file, a pending file outside the diff, or an unchanged pending file → preflight abort |
| L-6 | 1 s monitor interval; 6080 must close ≤ 2.5 s after a public flip | Measured ~0.7 s |
| L-7 | Phases that produced a report show only the summary, pass or fail. Terminal identity rules withhold `Signed in:`, display/user-name fields, and `name: "…"`. | Node |
| L-8 | GitHub token rule (`gh[opsur]_…`, `github_pat_…`) in the evidence sanitizer and the terminal | Node, plus the `--filter` CLI end to end |
| L-9 | Shared `c1_teardown_novnc`: marker first, then TERM → wait → KILL, then 6080 verified closed. An unverifiable teardown → `exit 4` → full cleanup. `c1_start_websockify` now tears down its own spawn on failure. | Gate cases including TERM-ignoring and unowned listeners |
| L-10 | On the `gh` path, the monitor and the gate re-read `gh` visibility; non-private → immediate teardown, unavailable → unverifiable | `gh` shim cases |
| L-11 | README documents both listeners (6080, 5173). `vite_host_guard` preflight check (required for R1). `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` is scrubbed. | Fixture cases, plus the pinned `e187311` config passes |
| I-3 | Installers run without GitHub/Codespaces credentials, secret-like variables, or npm/Node/Vite/Playwright overrides. They keep PATH, HOME, locale, proxy and CA-trust settings (required behind TLS-intercepting proxies; content is protected by lockfile integrity and R10). | Env and behavioural shim tests |
| I-4 | Test: a marked process with a mismatched start time is never signalled | Start-time-removal mutant now detected |
| I-5 | R10 requires the literal Playwright 1.65.1 and Chromium 159.0.8154.6 (confirmed from `playwright-core@1.65.1` `browsers.json`, revision 1255) as well as consistency | Node |
| Egress | `EGRESS_MARK` matches the pinned guards' `Unexpected request origin` (`cd33512` matched only the retired `… host` wording for unblocked requests) | Node |
| Tests | The pinned-SHA shell test was stale at `cd33512` (**72/1 FAIL** there). It now asserts the real pins. New section with real-history checks: ancestry, exact allowlist, production list, identical auth modules, pinned Vite guard. | — |

## Verification by the implementing session (not a substitute for review)

Environment: Anthropic cloud sandbox, Ubuntu 24.04.5, Node 22.23.4, x11vnc 0.9.16, ShellCheck 0.9.0. **Not a Codespace.** The Playwright and Chrome-for-Testing CDNs are blocked. No Microsoft or Entra traffic.

| Check | Result |
|---|---|
| Baseline `cd33512` (before integration) | Node 30/30; shell **72 passed / 1 failed** (stale pin test) |
| Shell suite `tests/test_c1_lib.sh` | **PASS, 115 / 0 / 0** |
| Node `tests/c1-evidence.test.mjs` | **PASS, 43/43** |
| Deliberate-break mutants (session tooling, scratch worktree) | **MUTSUM** |
| ShellCheck `-S warning`; `scripts/security_invariants.py` | PASS; PASS |
| Pinned `e187311` Vite 8.3.8 (`npm ci`), `--host 127.0.0.1`, stub backend on :8000 | Listener `127.0.0.1:5173` only. Loopback Host → 200, and `/projects/x` reaches the backend. Forwarded `Host: <name>-5173.app.github.dev` (± `:443`) → **403** for `/`, `/projects/x` (the backend is **not** reached), `/src/App.tsx` and `/@vite/client`; HMR WebSocket upgrade → 400 |
| Exact-head GitHub CI | See the PR #74 comment for this head |
| Offline Playwright via the harness | **UNVERIFIED**: ENVIRONMENT BLOCKED (no Chromium 159; CDN blocked) |
| Browser-level mutations (R2B) via the harness | **UNVERIFIED**: ENVIRONMENT BLOCKED (same) |
| End-to-end launcher in a simulated Codespace; launcher-level SIGINT/SIGTERM/SIGHUP/SIGKILL | **UNVERIFIED**: NOT RUN (the launcher cannot pass dependency install without Chromium; a substitute binary was refused). Covered by library-level and static tests only. |
| Real private Codespace | **UNVERIFIED**: NOT RUN |
| Chromium 159.0.8154.6 | **UNVERIFIED**: ENVIRONMENT BLOCKED |
| Live Entra (R6–R9) | **UNVERIFIED**: NOT RUN (not authorized) |

## Real-Codespace instrument run still required (independent)

1. The real unauthenticated status code and `Location` host for a private port. Only 401/403 or `https://github.com`/`*.github.com` are accepted; anything else fails **closed**.
2. That `X-Github-Token` reaches the decoy.
3. Refusal with 6080 pre-set to public.
4. Monitor teardown time.
5. The `gh codespace ports` values for private and org visibility.
6. **5173**: whether the edge passes the forwarded `Host` through to Vite (→ 403) or rewrites it to localhost (then the mitigation does not hold, and `/projects` would proxy to :8000).
7. Cleanup.
8. Chromium 159 offline suites and R2B, run by the harness.

## Requested verdict

APPROVE / REQUEST CHANGES for the consolidated round-2 remediation at this head, with findings. Separately, decide on the **App.tsx** change (R13). Keep PR #74 and PR #60 **draft**. Harness evidence cannot certify C1 until R13 is cleared by a separate review, the real-Codespace run and Chromium 159 are done, and a supervised session's review package is independently reviewed.

---

# Historical record

## Round 1 (`19f8851` → `27ec6d5`): what changed (by finding)

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

## Round 1 port privacy design (M-2; superseded where Round 2 says so): to be validated by the reviewer in a real private Codespace

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

## Round 1 verification (historical; at `6b45d0a` pins, not evidence for this head)

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

## Round 1 requested verdict (superseded by "Requested verdict" above)

APPROVE / REQUEST CHANGES for the remediation, with findings. Keep the PR in **draft**. Don't use this harness's evidence to certify C1 until all of the following are done:
1. the re-pin to the independently reviewed PR #60;
2. a real-Codespace instrument validation;
3. a separate independent review of a supervised session's review package.

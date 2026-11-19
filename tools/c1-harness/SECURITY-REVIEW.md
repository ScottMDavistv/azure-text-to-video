# Independent security review handoff — C1 validation harness

**Status: DRAFT. The consolidated round-2 remediation and the C1 desktop-blocker remediation (below) await a NEW independent full-diff review.** No implementing session listed below may review or approve it. This document is the implementer's account: treat it as claims, not evidence. **C1 REMAINS OPEN.**

## History

| Head | What | Who |
|---|---|---|
| `19f8851` | Original harness | Implementation session A |
| — | Independent review of `19f8851`: **REQUEST CHANGES** (H-1, H-2, M-1–M-4, L-1–L-5). [PR #74 comment 6960978238](https://github.com/ScottMDavistv/azure-text-to-video/pull/74#issuecomment-6960978238) | Reviewer session |
| `27ec6d5` | Round-1 remediation, plus N-1 | Session B (`session_01QdenYMweBLYZsb1EvGL4jt`) |
| — | Independent review of `27ec6d5`: **REQUEST CHANGES** (M-2a, N-2, L-6–L-11, I-3–I-5). [PR #74 comment 6963565503](https://github.com/ScottMDavistv/azure-text-to-video/pull/74#issuecomment-6963565503) | Session C (`session_01QdenYKTaRhz1YJ2XhMuDLF`) |
| `8d92366`…`cd33512` | Partial M-2a/N-2 fixes, then a reconciliation with PR #60 `e187311` and re-pin (PR #74 comments 6964141085 and 6964399093) | Another implementing session (commits authored `ScottMDavistv`) |
| this branch | **Consolidated round-2 remediation** (owner-authorized Option 1): integrates the stronger round-2 work from unpushed `55e1c63`/`e657ed2` onto `cd33512` without force-pushing | Session D (`session_01QdenYPt4giWiEjmFEVy9cD`) |

| `ee6ded8` | Round-2 head, plus PR #60 `c0cc725` trusted-main reconciliation | Implementing sessions |
| — | Independent review of `ee6ded8` (C1 desktop initialization blocker): **FAIL** (H-1, M-1–M-3, L-1–L-4) | Independent read-only reviewer session |
| this branch | **C1 desktop-blocker remediation** (P0, Issue #65): H-1, M-1–M-3, L-1–L-4 | Session E (`session_01QdenYPzwRjrGKn8DXdKNba`) |

**Reviewer independence:** sessions B, D and E and the `cd33512` session are implementers and **must not review**. Session C authored no code but proposed these fixes, so a **new** session is required.

## 2026-11-19 final focused remediation on `69a2bdc` (L-A, T-1, R-1, L-D): pending incremental independent review

This is the implementer's account. Treat it as claims, not evidence. The scope is `tools/c1-harness/**` only.

- **L-A (orphaned descendants).** The new `lib/c1-supervisor.py` runs every `c1_spawn` command under a persistent `PR_SET_CHILD_SUBREAPER` supervisor.
  - **Records:** the supervisor appends its identity to `SESSION/supervisors` before it forks. `c1_load_tree` treats recorded supervisors as tree leaders.
  - **Lifetime:** the supervisor exits only on `ECHILD`, then appends to the root-level `.session.*.reaped` proof.
  - **Cleanup:** TERM is forwarded by the supervisor. KILL is escalated as SIGUSR1 (the supervisor SIGKILLs its tree through pidfds after a start-time check). Cleanup waits for supervisors to exit, never SIGKILLs one, and skips its own ancestors (the watchdog runs cleanup inside its supervisor).
  - **Verification:** `c1_verify_supervisors` fails closed when a preserved supervisor is alive or is gone without a proof.
  - **Covered by regressions:** reviewer cases 3 and 4, orphan-after-scan, a killed supervisor, PID reuse, failed signals, interrupted cleanup and recovery, sampled supervisor lifetime, and four mutants (no subreaper ×2, early exit, a verify that ignores proofs).
- **Existing tests updated to the stronger behaviour:**
  - The reparented-child case now expects adoption and removal, not survival.
  - The exec'd-unmarked-leader case now expects its supervisor to terminate it.
  - The old descendant-capture mutation was removed: the capture is now defence in depth behind the supervisor, so removing it is no longer observable.
- **T-1.** The port-race foreign listener is armed by the test shell outside the harness tree. The supervised stand-in only touches a trigger whose path is embedded in it. The case asserts that the foreign `0.0.0.0` listener exists, is unmarked and is unattributed. A negative control proves that a race which never fires fails the case.
- **R-1.**
  - The monitor's `gh` read-back is now asynchronous and budgeted: one read in flight; 30 s after a `private` read, 10 s after a failed read started; nominal 120/h, cap 360/h.
  - Evidence goes stale at 75 s (`gh-stale`). The read-back timeout is 15 s; bootstrap is also 15 s, up from 5 s. Real `gh codespace ports` latency was **not** measured: it stays a real-Codespace verification item.
  - The monitor's pf-signin sweeps use the cached fresh evidence instead of one `gh` call per 0.2 s sweep. HTTPS probes stay at 0.4 s.
  - The `_` loop counter in `c1_teardown_novnc` (clobbered by `sleep`, which cut the TERM grace to about one step) was replaced.
- **L-D.** The README documents the same-UID trust boundary and its assumptions. No process-isolation claim is made.

## C1 desktop-blocker remediation (round 3, on `ee6ded8`)

Implementer's claims; verify independently. Nothing in `frontend/`, `backend/`, `scripts/`, `tests/` or `.github/` changed, and the pins are unchanged.

| Finding | Change | Regression coverage (`tests/test_c1_lib.sh`, `tests/c1-evidence.test.mjs`) |
|---|---|---|
| **H-1** x11vnc binds `[::]:5900` | `c1_cmd_x11vnc` adds `-rfbportv6 0 -no6`. The guard is unchanged in intent and stricter in evidence. | Flag test requires both flags. **Real** Xvfb + x11vnc with the exact argv under `strace`: no `AF_INET`/`AF_INET6` stream socket. Control: the `ee6ded8` flags **do** create `AF_INET6` (the probe is not blind on IPv6-less runners). |
| **M-1** IPv6-blind regressions | Above, plus fixture-backed IPv6 rows on real inodes, plus real-IPv6 cases that run wherever the kernel has IPv6 | IPv6-only `[::]:5900` (H-1 shape), dual-stack `0.0.0.0`+`[::]`, extra `[::1]`, IPv4-mapped decoding; real `[::]`/`[::1]` sockets on IPv6 hosts |
| **M-2** no diagnostic | Offending rows printed and recorded as `addr:port pid= comm= ino=` (sanitized; no environ/argv). `LISTENER_EVIDENCE`/`LISTENER_DETAIL` added to the meta allowlist. | Diagnostic content asserted; a forged detail is rejected by the allowlist |
| **M-3** fail-open evidence | `c1_listeners_for_pids` (ss `users:` parsing) is **removed**. Attribution is by socket inode over marker carriers, live recorded leaders, their setsid sessions and descendants. Strict parsers for `/proc/net/tcp{,6}` and `ss -Hltne`. Both sources are required and must agree. A failed or missing `ss`, a malformed row, an empty table while the other source has rows, IPv6 present without `tcp6`, an unreadable fd table or marker of a live recorded process, a malformed pid record, or a recorded leader that has lost its marker are all **failures**. `c1_port_free` returns 2 (unknown) instead of "free" when evidence is missing, so teardown and cleanup also fail closed. The `/dev/tcp` fallback (IPv4 loopback only) is removed. A preflight check `listener_evidence_available` is added (required for R1). **R11 PASS now also requires `LISTENER_EVIDENCE=verified`.** | The review's POC (real `0.0.0.0` listener + failing `ss`) now fails closed. Failing, garbage, empty and missing `ss`. Kernel-only and ss-only harness sockets. Address disagreement. Malformed/empty/headerless tables. Real EACCES on marker and fd table (unprivileged scanner, root harness process). Evidence: R11 is FAIL for every non-`verified` value. |
| **L-1** inherited fds | `c1_spawn` closes every fd above 2 before `exec` | A listening fd 37 in the parent is absent from the child. The parent holding it is still attributed. |
| **L-2** orphans | Marked processes that no live recorded leader accounts for are reported before noVNC | Re-parented marked leftover is named |
| **L-3** cleanup scope | Listener inodes **and** recorded `pid starttime` pairs observed during the session are kept past session-directory removal. `c1_verify_cleanup` fails while any recorded inode still listens, while any recorded process is still alive (including one that lost its marker and therefore cannot be signalled), or when evidence is unavailable. | Survivor inode fails verification and passes once closed. A marker-less recorded survivor is reported. Unavailable evidence fails. |
| **L-4** ShellCheck | SC1078 fixed (`test_c1_lib.sh`) | `shellcheck -S warning` clean on lib, launcher and tests |
| (new) tcp6 header | The kernel's `tcp6` header says `remote_address`. The strict header check accepts both spellings. | Fixture headers use the real `tcp6` spelling |
| Startup paths | | Adversarial x11vnc (serves the Unix socket, consumes the password, opens a wildcard TCP listener) aborts before noVNC, and cleanup verifies. A dying x11vnc leaves nothing behind. A foreign wildcard listener racing onto the noVNC port gives rc 2, `teardown-failed`. |

Known limits, for the reviewer: UDP listeners are not enumerated (no desktop component uses UDP). A harness process in another network namespace is invisible to these tables, and also unreachable through Codespaces forwarding. A listener created after the check is covered by the noVNC privacy re-checks and monitor, not by this check. An unrelated socket that reuses a recorded inode number after cleanup can produce a false cleanup FAILURE, never a false PASS.

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
| Node `tests/c1-evidence.test.mjs` | **PASS, 44/44** |
| Deliberate-break mutants (session tooling, scratch git worktree; covering every new protection, R13, the allowlist and pins, egress marking, and re-checks of H-2/M-3/L-1/N-1) | **38/38 detected**. The full run caught 36. The 2 survivors (N-2: a SKIPPED or unknown expected test next to an INCOMPLETE one was reported INCOMPLETE) exposed a missing test, which was added; both are now detected. |
| ShellCheck `-S warning`; `scripts/security_invariants.py` | PASS; PASS |
| Pinned `e187311` Vite 8.3.8 (`npm ci`), `--host 127.0.0.1`, stub backend on :8000 | Listener `127.0.0.1:5173` only. Loopback Host → 200, and `/projects/x` reaches the backend. Forwarded `Host: <name>-5173.app.github.dev` (± `:443`) → **403** for `/`, `/projects/x` (the backend is **not** reached), `/src/App.tsx` and `/@vite/client`; HMR WebSocket upgrade → 400 |
| Exact-head GitHub CI | Security Checks, Backend Python Tests, Frontend Quality Gates (results in the PR #74 comment for this head). *Frontend Quality Gates* runs the **pinned PR #60 suite** (the frontend tree at this head equals `e187311`) on Chromium **159.0.8154.6**: lockfile integrity, 7/7 source mutations, 14 offline tests, 7/7 browser mutations. That is CI evidence for the suite. It is **not** harness-executed R2B/R3 evidence and does not exercise the harness. |
| Offline Playwright via the harness | **UNVERIFIED**: ENVIRONMENT BLOCKED (no Chromium 159; CDN blocked) |
| Browser-level mutations (R2B) via the harness | **UNVERIFIED**: ENVIRONMENT BLOCKED (same) |
| End-to-end launcher in a simulated Codespace; launcher-level SIGINT/SIGTERM/SIGHUP/SIGKILL | **UNVERIFIED**: NOT RUN (the launcher cannot pass dependency install without Chromium; a substitute binary was refused). Covered by library-level and static tests only. |
| Real private Codespace | **UNVERIFIED**: NOT RUN |
| Chromium 159.0.8154.6 | **UNVERIFIED**: ENVIRONMENT BLOCKED |
| Live Entra (R6–R9) | **UNVERIFIED**: NOT RUN (not authorized) |

## 2026-11-17 L-6/L-9 focused follow-up — pending independent execution and review

The incremental follow-up after `a705ce2f` targets only `tools/c1-harness/**`.
The production launcher waits **0.2 s** between noVNC privacy sweeps; the
privacy monitor locally caps each unauthenticated/authenticated HTTPS probe at
**0.4 s**, treats the *first* ambiguous observation as unverifiable and tears
down, and locally bounds TERM/KILL grace to five 0.1-second steps each.
The launcher bootstrap and interactive privacy-gate defaults remain unchanged.
The L-9 monitor propagates failure (rc 2), writes `teardown-failed`, and its
watchdog checks the recorded launcher PID/start-time/session before sending TERM
and performing marker-scoped cleanup.

New source checks/regression cases in `tests/test_c1_lib.sh` target the actual
launcher interval, the slow two-probe path against the 2.5-second target,
monitor rc 2, synthetic PID ownership, and real marked-process watchdog
session cleanup. **These new harness cases were not independently executed in
the authoring environment** (repository clone unavailable); do not inherit
the historical 115/44/38 PASS counts as evidence for this new head. The three
GitHub Actions CI workflows do not run the harness-specific shell tests.
Treat L-6 normal Codespaces latency and L-9 full supervised teardown as
**UNVERIFIED** until an independent runner exercises the cases.

Before independent approval: execute `bash tools/c1-harness/tests/test_c1_lib.sh`,
`node --test tools/c1-harness/tests/c1-evidence.test.mjs`,
`shellcheck -S warning tools/c1-harness/lib/c1-lib.sh tools/c1-harness/c1-session.sh tools/c1-harness/tests/test_c1_lib.sh`,
and applicable security-invariant tests on this exact head. Measure monitor
shutdown with slow/reset/private-to-public flips in a real **private**
Codespace, including both 6080 and 5173, with no Entra authentication.
Keep the `frontend/src/App.tsx` R13 pending-review entry intact. PR #60 at
`b1e30d3` changes workflow files only; its environment isolation remains
UNVERIFIED until the owner checks GitHub's `design-management-control`
environment settings. Preserve manual workflow approvals and do not merge or
deploy from this evidence.

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
6. In this historical run, a background monitor re-probed every 15 s and stopped noVNC on public visibility or after three consecutive unverifiable checks. The current cadence and first-failure teardown policy are documented in the README.

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

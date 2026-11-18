#!/usr/bin/env bash
# Regression tests for the C1 harness library: startup, failure recovery and
# security restrictions. No network, no Microsoft, no Azure. Desktop
# integration tests run only when Xvfb/x11vnc/websockify/ss are installed.
set -Eeuo pipefail
umask 077
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/c1-lib.sh
source "$HERE/../lib/c1-lib.sh"

PASSED=0 FAILED=0 SKIPPED=0
TMP=$(mktemp -d)
cleanup_tests() { [[ -z "${FWDPID:-}" ]] || kill "$FWDPID" 2>/dev/null || true; c1_cleanup_all "$TMP/state" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup_tests EXIT

# Each case runs in a subshell with errexit+pipefail ACTIVE. It must NOT be
# invoked in an `if`/`&&`/`||` context: bash then ignores errexit for every
# command inside, including a subshell that re-runs `set -e`, so only a case's
# LAST command would be checked. Callers therefore use `set +e; run_case; rc=$?`.
run_case() { ( set -Eeuo pipefail; "$@" ) >"$TMP/out" 2>&1; }
# C1_TEST_FILTER=REGEX runs only matching cases (development aid for single cases
# and mutation runs; an unfiltered run is the only complete regression result).
# The first section always runs (its cases build the fixture for later ones).
FILTERABLE=0
want() { (( ! FILTERABLE )) || [[ -z "${C1_TEST_FILTER:-}" || "$1" =~ $C1_TEST_FILTER ]]; }
t() { # t NAME CMD...  (expects success)
  local name=$1 rc=0; shift
  want "$name" || return 0
  set +e; run_case "$@"; rc=$?; set -e
  if (( rc == 0 )); then PASSED=$((PASSED+1)); echo "ok   - $name"
  else FAILED=$((FAILED+1)); echo "FAIL - $name (exit $rc)"; sed 's/^/       /' "$TMP/out" | tail -15; fi
}
f() { # f NAME CMD...  (expects failure, but not a SIGPIPE/crash-style abort)
  local name=$1 rc=0; shift
  want "$name" || return 0
  set +e; run_case "$@"; rc=$?; set -e
  if (( rc == 0 )); then FAILED=$((FAILED+1)); echo "FAIL - $name (unexpectedly succeeded)"
  elif (( rc > 128 )); then FAILED=$((FAILED+1)); echo "FAIL - $name (killed by signal, exit $rc)"
  else PASSED=$((PASSED+1)); echo "ok   - $name"; fi
}
skip() { SKIPPED=$((SKIPPED+1)); echo "skip - $1"; }

# ------------------------------------------------------------------ fixture --
G=(git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false)
R="$TMP/repo"
mkdir -p "$R/frontend/src" "$R/frontend/e2e"
"${G[@]}" -C "$R" init -q
echo 'export const auth = 1' >"$R/frontend/src/entraAuth.ts"
echo 'app' >"$R/frontend/src/App.tsx"
"${G[@]}" -C "$R" add -A && "${G[@]}" -C "$R" commit -qm auth
AUTH=$(git -C "$R" rev-parse HEAD)
EXP=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs'
echo spec >"$R/frontend/e2e/entra-c1.spec.mjs"; echo cfg >"$R/frontend/playwright.config.mjs"
"${G[@]}" -C "$R" add -A && "${G[@]}" -C "$R" commit -qm pw
PW=$(git -C "$R" rev-parse HEAD)
echo 'tampered' >>"$R/frontend/src/entraAuth.ts"
"${G[@]}" -C "$R" commit -qam tamper
BAD=$(git -C "$R" rev-parse HEAD)
"${G[@]}" -C "$R" checkout -q -b side "$AUTH~0"
"${G[@]}" -C "$R" commit -q --allow-empty -m side
SIDE=$(git -C "$R" rev-parse HEAD)
"${G[@]}" -C "$R" checkout -q -b other "$PW"
mkdir -p "$R/tools/c1-harness" && echo h >"$R/tools/c1-harness/x.sh"
"${G[@]}" -C "$R" add -A && "${G[@]}" -C "$R" commit -qm harness

echo '# exact-head and trust checks'
t 'accepts approved auth->playwright pair with exact file set' c1_verify_commits "$R" "$AUTH" "$PW" "$EXP"
f 'rejects application change between auth and Playwright SHAs' c1_verify_commits "$R" "$AUTH" "$BAD" "$EXP"
f 'rejects auth SHA that is not an ancestor' c1_verify_commits "$R" "$SIDE" "$PW" "$EXP"
f 'rejects abbreviated SHAs' c1_verify_commits "$R" "${AUTH:0:12}" "$PW" "$EXP"
f 'rejects unknown commit' c1_verify_commits "$R" "$AUTH" "0000000000000000000000000000000000000000" "$EXP"

WT="$TMP/cache/approved"
t 'creates detached clean worktree at exact SHA' c1_prepare_worktree "$R" "$PW" "$WT"
t 'worktree HEAD is the exact SHA and detached' bash -c '[[ $(git -C "$1" rev-parse HEAD) == "$2" ]] && ! git -C "$1" symbolic-ref -q HEAD' _ "$WT" "$PW"
t 'cached worktree is reused idempotently' c1_prepare_worktree "$R" "$PW" "$WT"
echo x >>"$WT/frontend/src/App.tsx"
f 'rejects modified tracked file in cached worktree' c1_prepare_worktree "$R" "$PW" "$WT"
git -C "$WT" checkout -q -- .
echo x >"$WT/frontend/untracked.js"
f 'rejects untracked file in approved checkout' c1_verify_clean_tree "$WT" "$PW"
rm "$WT/frontend/untracked.js"
git -C "$WT" update-index --skip-worktree frontend/src/App.tsx
f 'rejects skip-worktree index entries' c1_verify_clean_tree "$WT" "$PW"
git -C "$WT" update-index --no-skip-worktree frontend/src/App.tsx
git -C "$WT" update-index --assume-unchanged frontend/src/App.tsx
f 'rejects assume-unchanged index entries' c1_verify_clean_tree "$WT" "$PW"
git -C "$WT" update-index --no-assume-unchanged frontend/src/App.tsx
git -C "$WT" checkout -q --detach "$BAD"
f 'rejects worktree whose HEAD moved' c1_verify_clean_tree "$WT" "$PW"
git -C "$WT" checkout -q --detach "$PW"
t 'clean approved checkout passes again' c1_verify_clean_tree "$WT" "$PW"
rm -rf "$WT" && mkdir -p "$WT" && echo junk >"$WT/junk"
t 'recovers from corrupt (unregistered) cached worktree' c1_prepare_worktree "$R" "$PW" "$WT"
t 'harness clean check passes on committed harness' c1_verify_harness_clean "$R" tools/c1-harness
echo y >>"$R/tools/c1-harness/x.sh"
f 'rejects modified harness files' c1_verify_harness_clean "$R" tools/c1-harness
git -C "$R" checkout -q -- tools
git -C "$R" update-index --assume-unchanged tools/c1-harness/x.sh; echo tampered >>"$R/tools/c1-harness/x.sh"
f 'rejects a modified harness file hidden by assume-unchanged (L-1)' c1_verify_harness_clean "$R" tools/c1-harness
git -C "$R" update-index --no-assume-unchanged tools/c1-harness/x.sh; git -C "$R" checkout -q -- tools
git -C "$R" update-index --skip-worktree tools/c1-harness/x.sh
f 'rejects a harness file flagged skip-worktree (L-1)' c1_verify_harness_clean "$R" tools/c1-harness
git -C "$R" update-index --no-skip-worktree tools/c1-harness/x.sh
t 'harness clean check passes again after flags are cleared' c1_verify_harness_clean "$R" tools/c1-harness

prod_pending_fixture() {   # App.tsx (production) is allowlisted between AUTH and PROD; App.tsx must then be pending review
  local exp2=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx' out
  "${G[@]}" -C "$R" checkout -q -b prodfix "$PW"
  echo 'app v2' >"$R/frontend/src/App.tsx"; "${G[@]}" -C "$R" commit -qam prod
  local prod; prod=$(git -C "$R" rev-parse HEAD)
  c1_verify_commits "$R" "$AUTH" "$prod" "$exp2"
  out=$(c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" 'frontend/src/App.tsx')
  [[ "$out" == "frontend/src/App.tsx@$(git -C "$R" rev-parse "$prod:frontend/src/App.tsx")" ]]
  if c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" ''; then echo 'unlisted production file accepted'; return 1; fi
  if c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" $'frontend/src/App.tsx\nfrontend/src/entraAuth.ts'; then echo 'pending file outside allowlist accepted'; return 1; fi
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$PW" "$EXP" '')" == none ]]
  "${G[@]}" -C "$R" checkout -q other
}
t 'uncertified production files in the approved diff must be recorded as pending review, with exact blob SHA' prod_pending_fixture
prod_pending_unchanged() {  # a pending path that does not differ AUTH->PW is rejected (keeps the list honest)
  local exp2=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx'
  ! c1_verify_prod_pending "$R" "$AUTH" "$PW" "$exp2" 'frontend/src/App.tsx'
}
t 'a pending-review path that is unchanged between AUTH and PW is rejected' prod_pending_unchanged
echo '# preflight checks'
FILTERABLE=1
t 'localhost resolving to loopback is accepted' c1_check_localhost $'127.0.0.1\n::1'
f 'localhost resolving to a non-loopback address is rejected' c1_check_localhost $'127.0.0.1\n10.1.2.3'
t 'loopback listener accepted' bash -c 'source "$1"; printf "127.0.0.1:6080\n" | c1_assert_loopback_only' _ "$HERE/../lib/c1-lib.sh"
f 'wildcard IPv4 listener rejected' bash -c 'source "$1"; printf "0.0.0.0:6080\n" | c1_assert_loopback_only' _ "$HERE/../lib/c1-lib.sh"
f 'wildcard IPv6 listener rejected' bash -c 'source "$1"; printf "[::]:6080\n" | c1_assert_loopback_only' _ "$HERE/../lib/c1-lib.sh"
f 'external interface listener rejected' bash -c 'source "$1"; printf "10.0.0.4:5901\n" | c1_assert_loopback_only' _ "$HERE/../lib/c1-lib.sh"
if command -v ss >/dev/null && command -v python3 >/dev/null; then
  PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
  t 'unused port reported free' c1_port_free "$PORT"
  python3 -c 'import socket,time,sys;s=socket.socket();s.bind(("127.0.0.1",int(sys.argv[1])));s.listen();time.sleep(30)' "$PORT" &
  LPID=$!; for _ in $(seq 1 50); do c1_port_free "$PORT" || break; sleep 0.1; done
  f 'busy port reported in use (fresh-server rule)' c1_port_free "$PORT"
  kill "$LPID" 2>/dev/null || true; wait "$LPID" 2>/dev/null || true
else skip 'port checks (ss/python3 missing)'; fi

echo '# security flags of desktop commands'
S0=/run/x/session.T
t 'Xvfb disables TCP and abstract sockets and requires an X cookie' bash -c 'source "$1"; c=$(c1_cmd_xvfb :99 "$2" | tr "\n" " "); [[ "$c" == *"-nolisten tcp"* && "$c" == *"-nolisten local"* && "$c" == *"-auth $2/Xauthority"* ]]' _ "$HERE/../lib/c1-lib.sh" "$S0"
t 'x11vnc: no TCP port, 0600 unix socket, one-time password file, no -nopw/-passwd' bash -c 'source "$1"; c=$(c1_cmd_x11vnc :99 "$2" | tr "\n" " "); [[ "$c" == *"-rfbport 0"* && "$c" == *"-unixsock $2/vnc.sock"* && "$c" == *"-passwdfile rm:$2/vncpass"* && "$c" != *"-localhost"* && "$c" != *"-nopw"* && "$c" != *"-passwd "* && "$c" != *"-shared"* ]]' _ "$HERE/../lib/c1-lib.sh" "$S0"
t 'websockify binds loopback only and targets the unix socket' bash -c 'source "$1"; c=$(c1_cmd_websockify 6080 "$2" | tr "\n" " "); [[ "$c" == *" 127.0.0.1:6080 "* && "$c" == *"--unix-target=$2/vnc.sock"* && "$c" != *"0.0.0.0"* ]]' _ "$HERE/../lib/c1-lib.sh" "$S0"
t 'one-time VNC password is 8 random alphanumerics and not reused' bash -c 'source "$1"; a=$(c1_gen_password); b=$(c1_gen_password); [[ "$a" =~ ^[A-Za-z0-9]{8}$ && "$b" =~ ^[A-Za-z0-9]{8}$ && "$a" != "$b" ]]' _ "$HERE/../lib/c1-lib.sh"
sigpipe() { local a; for _ in $(seq 1 50); do a=$(c1_gen_password); [[ "$a" =~ ^[A-Za-z0-9]{8}$ ]]; done; }
t 'password generation survives errexit+pipefail (SIGPIPE regression)' sigpipe
markerbig() {   # large environment must not hide the marker (pipe/SIGPIPE regression)
  local s=/tmp/c1-marker-test-$$ big; big=$(head -c 100000 /dev/zero | tr '\0' x)
  env BIG="$big" C1_HARNESS_SESSION="$s" sleep 30 & local p=$!; sleep 0.3
  c1_pid_has_marker "$p" "$s"; local r=$?; kill "$p"; return $r
}
t 'session marker found in a process with a very large environment' markerbig
t 'approved SHAs are pinned constants with no environment override' bash -c 'C1_PW_SHA_APPROVED=bad C1_AUTH_SHA_APPROVED=bad; source "$1"; [[ $C1_PW_SHA_APPROVED == e1873111e2304a7a739e0f35e2ecdca1d43de44d && $C1_AUTH_SHA_APPROVED == 81c352b2c6f7fdae4e94f2ff4e98a7e1c2bdf6c5 ]]' _ "$HERE/../lib/c1-lib.sh"
t 'toolchain pins: Playwright 1.65.1 and Chromium 159.0.8154.6, no environment override (I-5)' bash -c 'C1_PLAYWRIGHT_VERSION=9 C1_CHROMIUM_VERSION=9; source "$1"; [[ $C1_PLAYWRIGHT_VERSION == 1.65.1 && $C1_CHROMIUM_VERSION == 159.0.8154.6 ]]' _ "$HERE/../lib/c1-lib.sh"
t 'launcher scrubs mode/secret-adjacent variables before running tests' bash -c 'grep -q "unset C1_LIVE C1_UNCONFIGURED C1_MUTATION_MODE C1_EXPECTED_SHA" "$1" && grep -q "PWDEBUG DEBUG" "$1"' _ "$HERE/../c1-session.sh"
t 'launcher never passes trace/video/screenshot or mutation mode to Playwright' bash -c '! grep -Eq -- "--trace|--video|--screenshot|C1_MUTATION_MODE=1|--update-snapshots" "$1"' _ "$HERE/../c1-session.sh"

echo '# session lifecycle, cleanup and failure recovery'
export C1_STATE_ROOT="$TMP/state"
ROOT=$(c1_state_root)
lifecycle() {
  set -Eeuo pipefail   # regression: cleanup must survive errexit/pipefail
  local s; s=$(c1_session_new "$ROOT")
  [[ $(stat -c %a "$s") == 700 && $(stat -c %a "$ROOT") == 700 ]]
  [[ $(c1_session_status "$ROOT") == active ]]
  c1_spawn "$s" a sleep 300 >/dev/null
  c1_spawn "$s" b bash -c 'sleep 300 & sleep 300' >/dev/null
  env C1_HARNESS_SESSION="$s" nohup sleep 300 >/dev/null 2>&1 &   # orphan outside pidfile
  sleep 0.5
  [[ $(c1_marked_pids "$s" | wc -l) -ge 4 ]]
  echo 'secret-ish temp' >"$s/reports/raw.json"
  c1_cleanup_session "$ROOT" "$s"
  [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" && ! -e "$ROOT/active" ]]
  c1_cleanup_session "$ROOT" "$s"   # idempotent
}
t 'session dir 0700, all marked processes killed, files deleted, idempotent' lifecycle
stale() {
  set -Eeuo pipefail
  local s; s=$(c1_session_new "$ROOT")
  printf '999999 1 %s\n' "$s" >"$ROOT/active"        # crashed launcher
  env C1_HARNESS_SESSION="$s" nohup sleep 300 >/dev/null 2>&1 &
  sleep 0.3
  [[ $(c1_session_status "$ROOT") == stale ]]
  c1_cleanup_all "$ROOT"
  [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" && ! -e "$ROOT/active" ]]
}
t 'recovers an interrupted session: stale lock detected, orphans killed, files removed' stale
pidreuse() {
  local s; s=$(c1_session_new "$ROOT")
  printf '%s 1 %s\n' "$$" "$s" >"$ROOT/active"          # live pid, wrong start time
  [[ $(c1_session_status "$ROOT") == stale ]]
  c1_cleanup_all "$ROOT"
}
t 'PID reuse does not make a dead session look active' pidreuse
errexit_selfcheck() { false; true; }   # a failing middle assertion MUST fail the case
f 'test runner enforces every assertion in a case (errexit not masked)' errexit_selfcheck
alive() { kill -0 "$1" 2>/dev/null && [[ "$(c1_proc_stat_field "$1" 1)" != Z ]]; }
pidsformat() {
  local s p; s=$(c1_session_new "$ROOT"); p=$(c1_spawn "$s" a sleep 300)
  grep -Eq "^$p [0-9]+$" "$s/pids"                       # "pid starttime"
  [[ "$(awk '{print $2}' "$s/pids")" == "$(c1_proc_start "$p")" ]]
  c1_cleanup_session "$ROOT" "$s"; ! alive "$p"
}
t 'c1_spawn records pid AND kernel start time' pidsformat
ownedguard() {
  local s; s=$(c1_session_new "$ROOT")
  local bad; for bad in -1 0 1 abc '' '12 34' "$$"; do if c1_owned_pid "$bad" "$s"; then return 1; fi; done
}
t 'ownership guard rejects -1, 0, 1, non-numeric and the caller itself' ownedguard
reuse_with_start() {   # recorded PID+start time now belong to an UNRELATED process (no marker)
  local s v; s=$(c1_session_new "$ROOT")
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.3
  printf '%s %s\n' "$v" "$(c1_proc_start "$v")" >"$s/pids"; printf '999999 1 %s\n' "$s" >"$ROOT/active"
  c1_cleanup_all "$ROOT"; sleep 0.3
  local ok=0; alive "$v" && ok=1; kill "$v" 2>/dev/null || true
  (( ok == 1 ))
}
t 'stale recovery never signals an unrelated process (or its group) that reused a recorded PID' reuse_with_start
reuse_legacy_line() {  # legacy/garbage pids lines without a start time are never trusted
  local s v; s=$(c1_session_new "$ROOT")
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.3
  printf '%s\nnot-a-pid 5\n-1 1\n' "$v" >"$s/pids"; printf '999999 1 %s\n' "$s" >"$ROOT/active"
  c1_cleanup_all "$ROOT"; sleep 0.3
  local ok=0; alive "$v" && ok=1; kill "$v" 2>/dev/null || true
  (( ok == 1 ))
}
t 'stale recovery ignores pids lines without a verified start time' reuse_legacy_line
group_members() {      # a harness group member that dropped the marker (env -i) is still removed
  local s leader child; s=$(c1_session_new "$ROOT")
  leader=$(c1_spawn "$s" grp bash -c 'env -i sleep 300 & echo $! >"$0/child"; wait' "$s")
  for _ in $(seq 1 30); do [[ -s "$s/child" ]] && break; sleep 0.1; done
  child=$(cat "$s/child")
  ! c1_pid_has_marker "$child" "$s"                       # child carries no marker
  [[ "$(c1_proc_stat_field "$child" 3)" == "$leader" ]]   # but is in the harness group
  c1_cleanup_session "$ROOT" "$s"; sleep 0.3
  ! alive "$child" && ! alive "$leader"
}
t 'cleanup removes unmarked members of a verified harness process group' group_members
nonleader_marked() {   # a marked process inside an UNRELATED group: kill it, never its group
  local s lead; s=$(c1_session_new "$ROOT")
  setsid bash -c 'env C1_HARNESS_SESSION="$0" sleep 300 & exec sleep 300' "$s" </dev/null >/dev/null 2>&1 & lead=$!
  sleep 0.5
  local m; m=$(c1_marked_pids "$s"); [[ -n "$m" ]]
  [[ "$(c1_proc_stat_field "$m" 3)" == "$lead" && "$m" != "$lead" ]]
  c1_cleanup_session "$ROOT" "$s"; sleep 0.3
  local ok=0; { ! alive "$m" && alive "$lead"; } && ok=1
  kill "$lead" 2>/dev/null || true
  (( ok == 1 ))
}
t 'a marked non-leader is killed individually; its unrelated group leader survives' nonleader_marked
jobleader_marked() {   # a marked JOB leader in an unrelated session (e.g. `env C1_...=s cmd | other`)
  local s sess; s=$(c1_session_new "$ROOT")
  setsid bash -c 'set -m; env C1_HARNESS_SESSION="$0" sleep 300 | sleep 301 & wait' "$s" </dev/null >/dev/null 2>&1 & sess=$!
  sleep 0.8
  local m other; m=$(c1_marked_pids "$s"); [[ -n "$m" ]]
  [[ "$(c1_proc_stat_field "$m" 3)" == "$m" && "$(c1_proc_stat_field "$m" 4)" != "$m" ]]  # group leader, not session leader
  other=$(pgrep -g "$m" -x sleep | grep -vx "$m" | head -1); [[ -n "$other" ]]
  ! c1_pid_has_marker "$other" "$s"                       # unmarked process in that job's group
  c1_cleanup_session "$ROOT" "$s"; sleep 0.3
  local ok=0; { ! alive "$m" && alive "$other"; } && ok=1
  pkill -s "$sess" 2>/dev/null || true
  (( ok == 1 ))
}
t 'a marked job leader outside a harness session: only it is killed, never its job group' jobleader_marked
watchdog_kill() {      # L-3: launcher SIGKILL -> watchdog tears the session down
  local s fake w; s=$(c1_session_new "$ROOT")
  sleep 300 & fake=$!
  w=$(c1_spawn "$s" victim sleep 300)
  c1_start_watchdog "$ROOT" "$s" "$fake" "$HERE/../lib/c1-lib.sh"
  sleep 1.5; alive "$w"                                   # launcher alive: watchdog idle
  kill -KILL "$fake"; wait "$fake" 2>/dev/null || true
  for _ in $(seq 1 40); do [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]] && break; sleep 0.25; done
  [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]]
}
t 'watchdog removes processes and session files after the launcher is SIGKILLed' watchdog_kill
watchdog_zombie() {    # launcher SIGKILLed but NOT yet reaped by its parent (zombie)
  local s fake w parent; s=$(c1_session_new "$ROOT")
  # A parent that never reaps: its child plays the launcher.
  python3 -I -c 'import os,sys,time
p=os.fork()
if p==0: time.sleep(300); os._exit(0)
print(p, flush=True); time.sleep(60)' >"$s/fake" & parent=$!
  for _ in $(seq 1 30); do [[ -s "$s/fake" ]] && break; sleep 0.1; done
  fake=$(cat "$s/fake")
  w=$(c1_spawn "$s" victim sleep 300)
  c1_start_watchdog "$ROOT" "$s" "$fake" "$HERE/../lib/c1-lib.sh"; sleep 0.5
  kill -KILL "$fake"; sleep 0.3
  [[ "$(c1_proc_stat_field "$fake" 1)" == Z ]]           # still an unreaped zombie
  for _ in $(seq 1 40); do [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]] && break; sleep 0.25; done
  local ok=0; [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]] && ok=1
  kill "$parent" 2>/dev/null || true
  (( ok == 1 ))
}
t 'watchdog treats an unreaped (zombie) launcher as gone' watchdog_zombie
watchdog_normal() {    # normal cleanup stops the watchdog without a competing cleanup
  local s fake; s=$(c1_session_new "$ROOT")
  sleep 300 & fake=$!
  c1_start_watchdog "$ROOT" "$s" "$fake" "$HERE/../lib/c1-lib.sh"; sleep 0.5
  [[ -n "$(c1_marked_pids "$s")" ]]
  c1_cleanup_session "$ROOT" "$s"
  kill "$fake"; wait "$fake" 2>/dev/null || true
  [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]]
}
t 'normal cleanup stops the watchdog' watchdog_normal
cleanenv() {           # L-4 allowlist
  local out; local -a e
  mapfile -d '' -t e < <(PLAYWRIGHT_BROWSERS_PATH=/evil HTTPS_PROXY=http://evil.invalid NODE_OPTIONS=--require=/evil \
    CURL_CA_BUNDLE=/evil npm_config_registry=http://evil.invalid c1_clean_env)
  out=$("${e[@]}" env)
  [[ "$out" == *"PATH="* && "$out" == *"HOME="* ]]
  [[ "$out" != *PLAYWRIGHT_BROWSERS_PATH* && "$out" != *HTTPS_PROXY* && "$out" != *NODE_OPTIONS* && "$out" != *CURL_CA_BUNDLE* && "$out" != *npm_config* ]]
}
t 'test-phase environment is an allowlist (no PLAYWRIGHT_*, proxy, NODE_OPTIONS, CA or npm overrides)' cleanenv
t 'test-phase allowlist excludes Vite allowed-host overrides (L-11)' bash -c 'source "$1"; [[ " ${C1_ENV_ALLOW[*]} " != *VITE* ]] && grep -q "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS" "$2"' _ "$HERE/../lib/c1-lib.sh" "$HERE/../c1-session.sh"
installerenv() {        # I-3: credentials and tool overrides never reach installers
  local out; local -a e
  export GITHUB_TOKEN=ghu_fakefakefake GH_TOKEN=x GITHUB_CODESPACE_TOKEN=x CODESPACE_VSCODE_FOLDER=x ACTIONS_RUNTIME_TOKEN=x \
    MY_API_SECRET=x DB_PASSWORD=x NODE_OPTIONS=--require=/evil NODE_PATH=/evil npm_config_registry=http://evil.invalid \
    NPM_CONFIG_USERCONFIG=/evil PLAYWRIGHT_DOWNLOAD_HOST=http://evil.invalid PW_X=1 NODE_TLS_REJECT_UNAUTHORIZED=0 \
    __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=.app.github.dev VITE_X=1 \
    HTTPS_PROXY=http://proxy.invalid:3128 no_proxy=localhost NODE_EXTRA_CA_CERTS=/etc/ssl/proxy.pem SSL_CERT_FILE=/etc/ssl/x.pem
  mapfile -d '' -t e < <(c1_installer_env)
  out=$("${e[@]}" env)
  local v
  for v in GITHUB_TOKEN GH_TOKEN GITHUB_CODESPACE_TOKEN CODESPACE_VSCODE_FOLDER ACTIONS_RUNTIME_TOKEN MY_API_SECRET DB_PASSWORD \
           NODE_OPTIONS NODE_PATH npm_config_registry NPM_CONFIG_USERCONFIG PLAYWRIGHT_DOWNLOAD_HOST PW_X \
           NODE_TLS_REJECT_UNAUTHORIZED __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS VITE_X; do
    if grep -q "^$v=" <<<"$out"; then echo "leaked $v"; return 1; fi
  done
  for v in PATH HOME HTTPS_PROXY no_proxy NODE_EXTRA_CA_CERTS SSL_CERT_FILE; do grep -q "^$v=" <<<"$out" || { echo "dropped $v"; return 1; }; done
}
t 'installer environment drops GitHub tokens, secrets and tool overrides; keeps PATH/HOME/proxy/CA trust (I-3)' installerenv
installer_calls() {     # I-3 behavioural: npm ci / npm install / playwright install / install-deps all get the scrubbed env
  local fe="$TMP/ife/frontend" cache="$TMP/icache" bin="$TMP/ibin" f
  mkdir -p "$fe" "$cache" "$bin"
  printf 'node_modules/\n' >"$fe/.gitignore"; printf '{"lockfileVersion":3}\n' >"$fe/package-lock.json"
  "${G[@]}" -C "$fe" init -q; "${G[@]}" -C "$fe" add -A; "${G[@]}" -C "$fe" commit -qm fe
  for f in npm npx; do
    printf '#!/usr/bin/env bash\nenv >>"%s/%s.env"\nif [[ "$1" == ci || "$1" == install ]]; then mkdir -p node_modules/@playwright/test; printf %%s %s >node_modules/@playwright/test/package.json; fi\n' \
      "$TMP" "$f" "'{\"version\":\"1.64.0\"}'" >"$bin/$f"
    chmod +x "$bin/$f"
  done
  # The behavioral fixture never needs root. Mock only sudo -n to preserve
  # the real c1_ienv -> sudo -> env -> npx argument and credential boundary.
  # Real installer behavior and no_new_privileges are not changed.
  printf '#!/usr/bin/env bash\n' >"$bin/sudo"
  printf 'C1_SUDO_ARGS_LOG=%q\n' "$TMP/sudo.args" >>"$bin/sudo"
  cat >>"$bin/sudo" <<'C1_MOCK_SUDO'
[[ "${1:-}" == -n ]] || { echo 'mock sudo: missing -n' >&2; exit 91; }
shift
[[ "${1:-}" == env ]] || { echo 'mock sudo: expected env' >&2; exit 92; }
printf '%s\n' "$@" >"$C1_SUDO_ARGS_LOG"
exec "$@"
C1_MOCK_SUDO
  chmod +x "$bin/sudo"
  export PATH="$bin:$PATH" GITHUB_TOKEN=ghu_fakefakefake GH_TOKEN=x NODE_OPTIONS=--require=/evil MY_SECRET=x
  c1_install_node_deps "$fe" "$cache"
  [[ -s "$TMP/npm.env" && -s "$TMP/npx.env" ]]
  [[ $(grep -c '^PATH=' "$TMP/npm.env") -ge 2 && $(grep -c '^PATH=' "$TMP/npx.env") -ge 2 ]]   # npm ci+install; npx install+install-deps
  ! grep -Eq '^(GITHUB_TOKEN|GH_TOKEN|NODE_OPTIONS|MY_SECRET)=' "$TMP/npm.env" "$TMP/npx.env"
  # Negative paths: the mocked sudo must never forward unsanitized secrets,
  # and must refuse a call without -n or without the expected env boundary.
  if (( EUID != 0 )); then
    [[ -s "$TMP/sudo.args" ]] || { echo 'sudo shim not exercised'; return 1; }
    grep -Fxq env "$TMP/sudo.args" || return 1
    grep -Fxq -- '--no-install' "$TMP/sudo.args" || return 1
    ! grep -Eq '^(GITHUB_TOKEN|GH_TOKEN|NODE_OPTIONS|MY_SECRET)=' "$TMP/sudo.args"
  fi
  if "$bin/sudo" --invalid env true >"$TMP/sudo-negative.log" 2>&1; then return 1; fi
  if "$bin/sudo" -n true >>"$TMP/sudo-negative.log" 2>&1; then return 1; fi
}
t 'dependency installers never receive GITHUB_TOKEN or tool overrides (behavioural, I-3)' installer_calls
starttime_mismatch() {  # I-4: a MARKED process whose start time does not match is never signalled
  local s p real; s=$(c1_session_new "$ROOT"); p=$(c1_spawn "$s" a sleep 300); real=$(c1_proc_start "$p")
  c1_pid_has_marker "$p" "$s"
  if c1_owned_pid "$p" "$s" "$((real + 1))"; then return 1; fi
  c1_signal_owned KILL "$p" "$s" "$((real + 1))"; sleep 0.3
  alive "$p"                                          # survived the mismatched signal
  c1_owned_pid "$p" "$s" "$real"                      # positive control: matching start time is owned
  c1_cleanup_session "$ROOT" "$s"; ! alive "$p"
}
t 'a marked process with a mismatched kernel start time is never signalled (I-4)' starttime_mismatch
vitefix() {             # L-11 fixture: vitefix DIR [vite-config-text] [playwright-config-text]
  mkdir -p "$1/frontend"
  printf '%s\n' "${2:-"export default defineConfig({ server: { host: '127.0.0.1', port: 5173, strictPort: true } })"}" >"$1/frontend/vite.config.ts"
  printf '%s\n' "${3:-"webServer: { command: 'npm run dev -- --host 127.0.0.1' }"}" >"$1/frontend/playwright.config.mjs"
  printf '{"scripts":{"dev":"vite"}}\n' >"$1/frontend/package.json"
}
vite_guard() {
  local d="$TMP/vg"; rm -rf "$d"
  vitefix "$d/ok"; c1_check_vite_host_guard "$d/ok"
  vitefix "$d/a" "server: { host: '127.0.0.1', allowedHosts: true }"; if c1_check_vite_host_guard "$d/a"; then echo allowedHosts; return 1; fi
  vitefix "$d/b" "server: { host: '0.0.0.0' }"; if c1_check_vite_host_guard "$d/b"; then echo wildcard; return 1; fi
  vitefix "$d/c" "server: { host: true }"; if c1_check_vite_host_guard "$d/c"; then echo host-true; return 1; fi
  vitefix "$d/d" '' "webServer: { command: 'npm run dev -- --host 0.0.0.0' }"; if c1_check_vite_host_guard "$d/d"; then echo pw-host; return 1; fi
  vitefix "$d/e"; echo 'export default {}' >"$d/e/frontend/vite.config.mjs"; if c1_check_vite_host_guard "$d/e"; then echo two-configs; return 1; fi
  vitefix "$d/f"; printf '{"scripts":{"dev":"vite --allowed-hosts .app.github.dev"}}\n' >"$d/f/frontend/package.json"; if c1_check_vite_host_guard "$d/f"; then echo pkg; return 1; fi
  vitefix "$d/g"; rm "$d/g/frontend/vite.config.ts"; if c1_check_vite_host_guard "$d/g"; then echo no-config; return 1; fi
}
t 'Vite 5173 host-guard preflight: rejects allowedHosts, non-loopback binds, extra configs (L-11)' vite_guard
mkdir -p "$TMP/outside"
f 'safe_rm refuses paths outside the state root' c1_safe_rm "$TMP/outside" "$TMP/state"
f 'safe_rm refuses removing the parent itself' c1_safe_rm "$TMP/state" "$TMP/state"
t 'safe_rm ignores missing path' c1_safe_rm "$TMP/state/nope" "$TMP/state"

echo '# private desktop integration (loopback-only, password-protected)'
if command -v Xvfb >/dev/null && command -v x11vnc >/dev/null && command -v websockify >/dev/null \
   && command -v fluxbox >/dev/null && command -v ss >/dev/null && command -v python3 >/dev/null && [[ -d "$C1_NOVNC_WEB" ]]; then
  desktop() {
    set -Eeuo pipefail
    local s port pass d
    s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
    port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
    pass=$(c1_gen_password)
    c1_start_desktop "$s" "$port" "$pass"
    d=${DISPLAY#:}
    [[ ! -e "$s/vncpass" ]]                                   # one-time password file consumed
    [[ $(stat -c %a "$s/vnc.sock") == 600 && $(stat -c %a "$s/Xauthority") == 600 ]]
    if grep -q "@/tmp/.X11-unix/X$d" /proc/net/unix; then return 1; fi   # no abstract X socket
    # shellcheck disable=SC2046  # pid list is intentionally word-split
    [[ "$(c1_listeners_for_pids $(c1_marked_pids "$s"))" == "127.0.0.1:$port" ]]
    # Full RFB authentication over BOTH transports (H-2): only VNC auth offered,
    # none-auth refused, wrong password rejected, correct password accepted.
    python3 -I "$HERE/rfb_probe.py" unix "$s/vnc.sock" "$pass"
    python3 -I "$HERE/rfb_probe.py" ws "$port" "$pass"
    python3 - "$port" <<'PY'
import sys, urllib.request
r = urllib.request.urlopen('http://127.0.0.1:%s/vnc.html' % sys.argv[1], timeout=5)
assert r.status == 200
PY
    c1_assert_desktop_listeners "$s" "$port"
    unset C1_HARNESS_SESSION
    c1_cleanup_session "$ROOT" "$s"
    [[ -z "$(c1_marked_pids "$s")" && ! -e "$s" ]]
    c1_port_free "$port"
    [[ ! -S "/tmp/.X11-unix/X$d" ]] || ! pgrep -f "Xvfb :$d " >/dev/null
  }
  t 'desktop: loopback-only noVNC, VNC auth required, no X/VNC TCP, cleaned up' desktop
  refuse_busy() {
    local s port; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
    port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
    python3 -c 'import socket,time,sys;s=socket.socket();s.bind(("127.0.0.1",int(sys.argv[1])));s.listen();time.sleep(20)' "$port" &
    local lp=$!; sleep 0.5
    local rc=0; c1_start_desktop "$s" "$port" "$(c1_gen_password)" || rc=$?
    kill "$lp"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
    (( rc != 0 ))
  }
  t 'desktop refuses to start when the noVNC port is already taken' refuse_busy
else
  skip 'desktop integration (install: xvfb x11vnc novnc websockify fluxbox xauth iproute2)'
fi

echo '# Codespaces port privacy verification (M-2; offline mock of GitHub forwarding)'
# shellcheck disable=SC2034  # C1_PRIVACY_TIMEOUT is read by the library functions under test
freeport() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
if command -v openssl >/dev/null && command -v curl >/dev/null && command -v python3 >/dev/null && command -v ss >/dev/null; then
  PV="$TMP/privacy"; mkdir -p "$PV/state"
  openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=localhost -addext subjectAltName=DNS:localhost \
    -keyout "$PV/key.pem" -out "$PV/cert.pem" -days 1 >/dev/null 2>&1
  FWD=$(freeport); DP=$(freeport)
  TOKEN="ghu_$(c1_nonce)"; printf '%s' "$TOKEN" >"$PV/state/token"; echo private >"$PV/state/mode"; echo User >"$PV/state/owner"
  python3 -I "$HERE/mock_forwarder.py" "$FWD" "$DP" "$PV/cert.pem" "$PV/key.pem" "$PV/state" >/dev/null 2>&1 &
  FWDPID=$!
  for _ in $(seq 1 50); do c1_port_free "$FWD" || break; sleep 0.1; done
  BASE="https://localhost:$FWD"; CA="$PV/cert.pem"
  c1_write_header_file "$PV/hdr" X-Github-Token "$TOKEN"
  c1_write_header_file "$PV/api.hdr" Authorization "token $TOKEN"
  c1_write_header_file "$PV/bad.hdr" X-Github-Token "ghu_wrong"
  mode() { echo "$1" >"$PV/state/mode"; }
  # shellcheck disable=SC2034  # C1_PRIVACY_TIMEOUT is read by the library
  decoy_expect() {  # decoy_expect MODE EXPECTED_STATE [HDR]
    mode "$1"; local s rc=0; s=$(c1_session_new "$ROOT"); C1_PRIVACY_TIMEOUT=8
    c1_verify_private_with_decoy "$s" "$DP" "$BASE" "${3:-$PV/hdr}" "$CA" || rc=$?
    local st=$C1_PRIVACY_STATE
    c1_port_free "$DP"; [[ ! -e "$s/decoy" ]]            # decoy always stopped and removed
    c1_cleanup_session "$ROOT" "$s"; mode private
    if [[ $2 == private ]]; then (( rc == 0 )) && [[ $st == private ]]; else (( rc != 0 )) && [[ $st == "$2" ]]; fi
  }
  t 'private port: unauthenticated probe denied, token probe reaches decoy -> verified' decoy_expect private private
  t 'public port: unauthenticated probe reaches decoy -> refused, decoy stopped' decoy_expect public public
  t 'sign-in interstitial (unauthenticated 2xx) is treated as exposure -> refused' decoy_expect interstitial public
  t 'port not forwarded (502) -> unverified, refused after timeout' decoy_expect down unverified
  t 'ambiguous unauthenticated 503 with successful token probe -> unverified, refused' decoy_expect ambiguous unverified
  t 'authenticated probe reaches a different service (no nonce) -> unverified' decoy_expect wrongport unverified
  t 'wrong token -> authenticated probe never reaches decoy -> unverified' decoy_expect private unverified "$PV/bad.hdr"
  # M-2a: only an explicit 401/403 or a validated https redirect to github.com counts as denial.
  t 'unauthenticated 401 is a positive denial -> verified (M-2a)' decoy_expect unauth401 private
  t 'unauthenticated 403 is a positive denial -> verified (M-2a)' decoy_expect unauth403 private
  t 'unauthenticated redirect to an https github.com subdomain -> verified (M-2a)' decoy_expect subdomain private
  t 'unauthenticated connection reset (flaky edge) -> unverified, never verified (M-2a)' decoy_expect flaky unverified
  t 'unauthenticated 5xx -> unverified, never verified (M-2a)' decoy_expect unauth5xx unverified
  t 'unauthenticated 404 -> unverified (M-2a)' decoy_expect unauth404 unverified
  t 'unauthenticated redirect to a non-GitHub host -> unverified (M-2a)' decoy_expect badredirect unverified
  t 'unauthenticated redirect to a github.com look-alike host -> unverified (M-2a)' decoy_expect lookalike unverified
  t 'unauthenticated redirect with github.com userinfo to another host -> unverified (M-2a)' decoy_expect userinfo unverified
  t 'unauthenticated redirect to plain-http github.com -> unverified (M-2a)' decoy_expect httpredirect unverified
  t 'unauthenticated redirect without Location -> unverified (M-2a)' decoy_expect noloc unverified
  # shellcheck disable=SC2034  # read by the library
  hang_timeout() { C1_PROBE_MAX_TIME=2; decoy_expect hang unverified; }
  t 'unauthenticated probe timeout -> unverified (M-2a)' hang_timeout
  unauth_table() {
    local c l bad
    c1_unauth_denied 401; c1_unauth_denied 403
    c1_unauth_denied 302 'https://github.com/login?return_to=x'; c1_unauth_denied 307 'https://auth.github.com/x'
    c1_unauth_denied 302 'https://GitHub.com:443/login'; c1_unauth_denied 308 'https://github.com'
    for bad in '000' '200' '204' '304 https://github.com/' '400' '404' '407' '429' '500' '502' '503' '504' '302' \
      '302 https://evil.example/' '302 https://github.com.evil.example/' '302 https://github.com@evil.example/' \
      '302 http://github.com/login' '302 https://evilgithub.com/' '302 https://github.com:8443/' '302 https://.github.com/' \
      '302 https://github.com./' '302 //github.com/' '302 https://github.com?x' '302 https://a..github.com/' \
      '302 https://-x.github.com/' '302 https://github.co/' '302 https://github.com%2eevil.example/' '302 javascript:github.com' \
      '301 https://login.githubusercontent.com/' '302 https://github.dev/pf-signin'; do
      read -r c l <<<"$bad"
      if c1_unauth_denied "$c" "${l:-}"; then echo "accepted as denial: $bad"; return 1; fi
    done
  }
  t 'unauthenticated-denial classifier: only 401/403 or https redirect to github.com[/subdomain] (M-2a)' unauth_table
  # shellcheck disable=SC2034  # read by the library
  transport_failures() {
    C1_PROBE_MAX_TIME=3
    local dead; dead=$(freeport)
    if c1_privacy_check "https://c1-nxdomain.invalid" /x marker "$PV/hdr" "$CA"; then return 1; fi
    [[ $C1_PRIVACY_STATE == unverified ]]                  # DNS failure
    if c1_privacy_check "https://localhost:$dead" /x marker "$PV/hdr" "$CA"; then return 1; fi
    [[ $C1_PRIVACY_STATE == unverified ]]                  # connection refused
    if c1_privacy_check "$BASE" /x marker "$PV/hdr"; then return 1; fi
    [[ $C1_PRIVACY_STATE == unverified ]]                  # TLS verification failure (untrusted CA)
  }
  t 'DNS failure, refused connection and TLS failure are unverified, never private (M-2a)' transport_failures
  # shellcheck disable=SC2034  # C1_PRIVACY_TIMEOUT is read by the library
  token_argv() {   # the GitHub token never appears in any process command line
    mode slow; local s; s=$(c1_session_new "$ROOT"); C1_PRIVACY_TIMEOUT=10
    c1_verify_private_with_decoy "$s" "$DP" "$BASE" "$PV/hdr" "$CA" >/dev/null 2>&1 & local bg=$!
    local seen=0 f
    for _ in $(seq 1 30); do
      for f in /proc/[0-9]*/cmdline; do if grep -qaF -- "$TOKEN" "$f" 2>/dev/null; then seen=1; fi; done
      sleep 0.2
    done
    wait "$bg" || true; c1_cleanup_session "$ROOT" "$s"; mode private
    (( seen == 0 ))
  }
  t 'GitHub token is passed via a 0600 header file, never in argv' token_argv
  no_password_path() {   # the VNC password is printed only on the success branch of start_private_desktop
    local f="$HERE/../c1-session.sh"
    [[ $(grep -c 'print_access "$pass"' "$f") == 1 ]]
    awk '/if ! start_private_desktop "\$pass"; then/{a=1} a&&/^    else$/{e=1} a&&/print_access "\$pass"/{exit !e} END{if(!a)exit 1}' "$f"
    grep -A2 'if ! start_private_desktop "$pass"; then' "$f" | grep -q "pass=''"
  }
  t 'VNC password is disclosed only after privacy verification succeeded (M-2a)' no_password_path
  owner_type() {
    [[ "$(c1_repo_owner_type owner/repo "$PV/api.hdr" "$CA" "$BASE")" == User ]]
    echo Organization >"$PV/state/owner"
    [[ "$(c1_repo_owner_type owner/repo "$PV/api.hdr" "$CA" "$BASE")" == Organization ]]
    echo User >"$PV/state/owner"
    [[ -z "$(c1_repo_owner_type owner/repo "$PV/bad.hdr" "$CA" "$BASE")" ]]   # unauthorized -> unknown
  }
  t 'repository owner type is read with the token (org visibility cannot be excluded otherwise)' owner_type
  if command -v Xvfb >/dev/null && command -v x11vnc >/dev/null && command -v websockify >/dev/null && [[ -d "$C1_NOVNC_WEB" ]]; then
    novnc_recheck() {
      local s ws; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      c1_start_display_vnc "$s" "$(c1_gen_password)"; ws=$(c1_start_websockify "$s" "$DP")
      c1_verify_private_novnc "$BASE" "$PV/hdr" "$CA"
      mode public
      local rc=0; c1_verify_private_novnc "$BASE" "$PV/hdr" "$CA" || rc=$?
      local st=$C1_PRIVACY_STATE; mode private
      unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( rc != 0 )) && [[ $st == public ]]
    }
    t 'noVNC re-check passes when private and fails closed when public' novnc_recheck
    # gh shim (L-10): reads visibility from $PV/state/ghvis; "fail" makes gh fail.
    mkdir -p "$TMP/ghbin"
    printf '#!/usr/bin/env bash\nv=$(cat %q 2>/dev/null || echo fail)\n[[ $v == fail ]] && exit 1\nprintf "[{\\"sourcePort\\":%%s,\\"visibility\\":\\"%%s\\"}]\\n" "$(cat %q)" "$v"\n' \
      "$PV/state/ghvis" "$PV/state/ghport" >"$TMP/ghbin/gh"
    chmod +x "$TMP/ghbin/gh"; echo "$DP" >"$PV/state/ghport"; echo private >"$PV/state/ghvis"
    usegh() { export PATH="$TMP/ghbin:$PATH" CODESPACE_NAME=c1-test; }
    now_us() { local t=$EPOCHREALTIME; printf '%s' "${t/./}"; }
    # closed_within PORT SECONDS -> 0 if PORT stops listening within SECONDS (50 ms polling)
    closed_within() {
      local port=$1 limit_us; limit_us=$(awk -v s="$2" 'BEGIN{printf "%d", s*1000000}')
      local t0; t0=$(now_us)
      while (( $(now_us) - t0 <= limit_us )); do c1_port_free "$port" && { echo "closed after $(( ($(now_us) - t0) / 1000 )) ms"; return 0; }; sleep 0.05; done
      echo "still listening after $2 s"; return 1
    }
    # start_mon SESSION MODE REQGH -> starts display, noVNC on DP and a 1 s monitor; prints "ws wsst"
    start_mon() {
      local s=$1 ws wsst
      c1_start_display_vnc "$s" "$(c1_gen_password)" >/dev/null; ws=$(c1_start_websockify "$s" "$DP"); wsst=$(c1_proc_start "$ws")
      # shellcheck disable=SC2016
      c1_spawn "$s" privacy-monitor bash -c 'source "$1"; c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "$7" 1 "$8" "$9"' \
        _ "$HERE/../lib/c1-lib.sh" "$s" "$BASE" "$PV/hdr" "$ws" "$wsst" "$CA" "$DP" "$3" >/dev/null
      printf '%s %s' "$ws" "$wsst"
    }
    monitor() {          # L-6: with a 1 s interval a public flip must close 6080 within 2.5 s
      local s ws wsst; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 0)"
      sleep 2.5; alive "$ws"; [[ ! -e "$s/privacy-failed" ]]   # stays up while private
      mode public
      local ok=0; closed_within "$DP" 2.5 && ok=1
      for _ in $(seq 1 10); do [[ -e "$s/privacy-failed" ]] && break; sleep 0.1; done
      for _ in $(seq 1 30); do alive "$ws" || break; sleep 0.1; done      # TERM -> wait -> KILL completes
      [[ -e "$s/privacy-failed" ]] && grep -q ' public$' "$s/privacy-failed" && ! alive "$ws" || { ok=0; echo "marker: $(cat "$s/privacy-failed" 2>&1); ws alive: $(alive "$ws" && echo yes || echo no)"; }
      mode private; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor closes noVNC within 2.5 s of the port turning public (1 s interval, L-6)' monitor
    monitor_ambiguous() { # M-2a in the monitor: an edge that resets unauthenticated clients is never "private"
      local s ws wsst; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 0)"
      sleep 1.5; alive "$ws"
      mode flaky
      local ok=0; closed_within "$DP" 8 && ok=1
      grep -q ' unverified$' "$s/privacy-failed" || ok=0
      mode private; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor tears noVNC down when unauthenticated denial becomes ambiguous (M-2a)' monitor_ambiguous
    monitor_gh() {        # L-10: gh read-back changing to org (or public) tears down immediately
      local vis=$1 s ws wsst; usegh; echo private >"$PV/state/ghvis"
      s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 1)"
      sleep 2.5; alive "$ws"; [[ ! -e "$s/privacy-failed" ]]    # gh private + probe private: stays up
      echo "$vis" >"$PV/state/ghvis"                            # probe still says private
      local ok=0; closed_within "$DP" 2.5 && ok=1
      grep -q " gh-visibility-$vis\$" "$s/privacy-failed" || ok=0
      echo private >"$PV/state/ghvis"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor re-reads gh: a change to organization visibility tears noVNC down (L-10)' monitor_gh org
    t 'privacy monitor re-reads gh: a change to public visibility tears noVNC down (L-10)' monitor_gh public
    monitor_gh_unavailable() {   # L-10: losing the gh read-back is unverifiable -> torn down after 3 misses
      local s ws wsst; usegh; echo private >"$PV/state/ghvis"
      s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 1)"
      sleep 1.5; alive "$ws"
      echo fail >"$PV/state/ghvis"
      local ok=0; closed_within "$DP" 8 && ok=1
      grep -q ' gh-unavailable$' "$s/privacy-failed" || ok=0
      echo private >"$PV/state/ghvis"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor tears noVNC down when the gh read-back becomes unavailable (L-10)' monitor_gh_unavailable
    monitor_user_path_ignores_gh() {   # REQUIRE_GH=0 (user-owned repo path): gh is not consulted
      local s ws wsst; usegh; echo org >"$PV/state/ghvis"
      s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 0)"
      sleep 3; local ok=0; alive "$ws" && [[ ! -e "$s/privacy-failed" ]] && ok=1
      echo private >"$PV/state/ghvis"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor on the user-owned-repository path relies on the probe only' monitor_user_path_ignores_gh
    # ------------------------------------------------------------ L-9 gate --
    # shellcheck disable=SC2034  # C1_NOVNC_RETRIES is read by the library
    gate() {     # gate MODE GHVIS REQGH EXPECTED_RC [REASON]
      local s ws wsst rc=0; usegh; echo "$2" >"$PV/state/ghvis"; C1_NOVNC_RETRIES=2
      s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      c1_start_display_vnc "$s" "$(c1_gen_password)" >/dev/null; ws=$(c1_start_websockify "$s" "$DP"); wsst=$(c1_proc_start "$ws")
      mode "$1"
      c1_privacy_gate "$s" "$DP" "$BASE" "$PV/hdr" "$ws" "$wsst" "$3" "$CA" || rc=$?
      local ok=1
      if (( $4 == 0 )); then { (( rc == 0 )) && alive "$ws" && ! c1_port_free "$DP" && [[ ! -e "$s/privacy-failed" ]]; } || ok=0
      else { (( rc == $4 )) && c1_port_free "$DP" && ! alive "$ws" && grep -q " ${5}\$" "$s/privacy-failed"; } || ok=0; fi
      mode private; echo private >"$PV/state/ghvis"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy gate passes while private (L-9)' gate private private 0 0
    t 'privacy gate on a public port: marker written, noVNC stopped, 6080 verified closed (L-9)' gate public private 0 1 public
    t 'privacy gate on an ambiguous probe: torn down and verified closed (L-9, M-2a)' gate unauth5xx private 0 1 unverified
    t 'privacy gate re-reads gh: org visibility -> torn down (L-9, L-10)' gate private org 1 1 gh-visibility-org
    t 'privacy gate re-reads gh: unavailable read-back -> torn down (L-9, L-10)' gate private fail 1 1 gh-unavailable
    gate_monitor_flag() {   # the monitor's marker alone closes the gate (and the teardown is re-verified)
      local s ws wsst rc=0; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      c1_start_display_vnc "$s" "$(c1_gen_password)" >/dev/null; ws=$(c1_start_websockify "$s" "$DP"); wsst=$(c1_proc_start "$ws")
      printf 'x public\n' >"$s/privacy-failed"
      c1_privacy_gate "$s" "$DP" "$BASE" "$PV/hdr" "$ws" "$wsst" 0 "$CA" || rc=$?
      local ok=0; (( rc == 1 )) && c1_port_free "$DP" && ! alive "$ws" && ok=1
      unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"; (( ok == 1 ))
    }
    t 'privacy gate honours the monitor marker and still verifies 6080 closed (L-9)' gate_monitor_flag
    gate_term_ignored() {   # websockify ignoring TERM: the KILL step must still close the port
      local s ws wsst rc=0; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      ws=$(c1_spawn "$s" stubborn python3 -I -c 'import signal,socket,sys,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(300)' "$DP")
      for _ in $(seq 1 50); do c1_port_free "$DP" || break; sleep 0.1; done
      wsst=$(c1_proc_start "$ws")
      mode public
      c1_privacy_gate "$s" "$DP" "$BASE" "$PV/hdr" "$ws" "$wsst" 0 "$CA" || rc=$?
      local ok=0; (( rc == 1 )) && c1_port_free "$DP" && ! alive "$ws" && ok=1
      mode private; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"; (( ok == 1 ))
    }
    t 'privacy gate KILLs a TERM-ignoring noVNC and verifies 6080 closed (L-9)' gate_term_ignored
    # shellcheck disable=SC2034  # C1_NOVNC_RETRIES is read by the library
    gate_teardown_unverifiable() {   # an unowned listener keeps the port open: fail closed (rc 2)
      local s rc=0 lp; s=$(c1_session_new "$ROOT"); C1_NOVNC_RETRIES=1
      python3 -I -c 'import socket,sys,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("127.0.0.1",int(sys.argv[1])));s.listen();time.sleep(300)' "$DP" & lp=$!
      for _ in $(seq 1 50); do c1_port_free "$DP" || break; sleep 0.1; done
      mode public
      c1_privacy_gate "$s" "$DP" "$BASE" "$PV/hdr" "$lp" "$(c1_proc_start "$lp")" 0 "$CA" || rc=$?
      local ok=0; (( rc == 2 )) && grep -q ' teardown-failed$' "$s/privacy-failed" && alive "$lp" && ok=1   # unowned process never signalled
      kill "$lp"; wait "$lp" 2>/dev/null || true
      mode private; c1_cleanup_session "$ROOT" "$s"; (( ok == 1 ))
    }
    t 'privacy gate fails closed (rc 2) when 6080 cannot be verified closed; never signals an unowned process (L-9)' gate_teardown_unverifiable
    t 'launcher privacy gate uses the shared teardown and aborts on an unverified teardown (L-9)' bash -c \
      'grep -q "c1_privacy_gate \"\$S\" \"\$C1_NOVNC_PORT\" \"\$BASE\" \"\$HDR\" \"\$WS\" \"\$WSST\" \"\$REQ_GH\"" "$1" && grep -q "exit 4" "$1" && ! grep -q "c1_signal_owned TERM \"\$WS\"" "$1"' _ "$HERE/../c1-session.sh"
    no_desktop_on_failed_verification() {   # M-2a: an ambiguous edge never yields a served desktop
      local m s rc
      # shellcheck disable=SC2034  # read by the library
      C1_PRIVACY_TIMEOUT=4
      for m in flaky unauth5xx badredirect; do
        s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
        c1_start_display_vnc "$s" "$(c1_gen_password)" >/dev/null
        mode "$m"; rc=0; c1_verify_private_with_decoy "$s" "$DP" "$BASE" "$PV/hdr" "$CA" || rc=$?
        (( rc != 0 )) && [[ $C1_PRIVACY_STATE == unverified ]]
        c1_port_free "$DP"
        # shellcheck disable=SC2046
        [[ -z "$(c1_listeners_for_pids $(c1_marked_pids "$s"))" ]]   # no TCP listener: no desktop reachable
        if pgrep -f "websockify .*127.0.0.1:$DP" >/dev/null; then return 1; fi
        mode private; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      done
    }
    t 'failed (ambiguous) privacy verification leaves no desktop listener and no noVNC (M-2a)' no_desktop_on_failed_verification
  else skip 'noVNC privacy re-check / monitor (desktop packages missing)'; fi
  kill "$FWDPID" 2>/dev/null || true
else
  skip 'privacy verification (openssl/curl/python3/ss missing)'
fi
supervised_requires_codespaces() {
  command -v script >/dev/null || return 0
  local out; out=$(env -u CODESPACES script -qec "bash '$HERE/../c1-session.sh'" /dev/null 2>&1 </dev/null || true)
  [[ "$out" == *'supervised mode runs only in a GitHub Codespace'* ]]
}
t 'supervised mode refuses to run outside a Codespace (no unverifiable privacy)' supervised_requires_codespaces
t 'launcher never runs a verification as `cmd && check` (errexit-exempt, fail-open) (N-1)' bash -c '! grep -nE "^[^#]*&& check [a-z_]+" "$1"' _ "$HERE/../c1-session.sh"
t 'launcher has no typed-confirmation privacy fallback' bash -c '! grep -q "Type PRIVATE" "$1" "$2"' _ "$HERE/../c1-session.sh" "$HERE/../lib/c1-lib.sh"

# L-6 and L-9: test the production monitor setting and the actual fail-closed
# propagation, not only a test helper's injectable one-second interval.
t 'L-6: live launcher uses bounded 200ms monitoring, not test-only timing' bash -c \
  'grep -Fq '\''c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "" 0.2 "$7" "$8"'\'' "$1" && ! grep -Fq '\''c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "" 15 "$7" "$8"'\'' "$1"' _ "$HERE/../c1-session.sh"

monitor_teardown_failure_propagates() {
  # No network or actual listener: force a public observation and a teardown
  # that cannot verify port closure. The monitor MUST return a failure (rc 2).
  c1_owned_pid() { return 0; }
  c1_privacy_check() { C1_PRIVACY_STATE=public; return 1; }
  c1_teardown_novnc() { return 1; }
  sleep() { :; }
  local rc=0
  c1_privacy_monitor_loop "$TMP" example.invalid /nonexistent 999 1 '' 1 6080 0 || rc=$?
  (( rc == 2 ))
}
t 'L-9: monitor propagates unverified teardown as rc 2, never success' monitor_teardown_failure_propagates

watchdog_escalates_failed_teardown() {
  local d="$TMP/watchdog-l9"; mkdir -p "$d/session" "$d/state"
  printf 'now teardown-failed\n' >"$d/session/privacy-failed"
  printf '4242 5678 %s\n' "$d/session" >"$d/state/active"
  c1_proc_start() { printf '5678\n'; }
  c1_proc_stat_field() { printf 'S\n'; }
  c1_cleanup_session() { :; }
  kill() { [[ "$1" == -TERM && "$2" == 4242 ]] && printf 'owner signalled\n' >"$d/signalled"; }
  c1_watchdog_loop "$d/state" "$d/session" 4242 5678
  [[ "$(cat "$d/signalled")" == 'owner signalled' ]]
  # Never signal a stale or mismatched active record.
  rm "$d/signalled"
  printf '4242 9999 %s\n' "$d/session" >"$d/state/active"
  c1_watchdog_loop "$d/state" "$d/session" 4242 5678
  [[ ! -e "$d/signalled" ]]
}
t 'L-9: watchdog aborts only the exact recorded owner after teardown-failed marker' watchdog_escalates_failed_teardown

# L-6: exercise the real privacy classifier against two slow responses,
# at the locally scoped monitor timeout, not an unrealistically instant stub.
monitor_slow_authenticated_probe() {
  local d="$TMP/slow-monitor"; mkdir -p "$d"
  printf 'X-Github-Token: fixture\n' >"$d/header"
  c1_owned_pid() { return 0; }
  c1_https_probe() {
    sleep "$C1_PROBE_MAX_TIME"
    if [[ -z "${3:-}" ]]; then printf '403\n'; else printf '000\n'; fi
  }
  c1_teardown_novnc() {
    sleep 0.5
    printf '%s\n' "$5" >"$1/privacy-failed"
  }
  local before=$EPOCHREALTIME rc=0 elapsed
  c1_privacy_monitor_loop "$d" https://example.invalid "$d/header" 98765 1 '' 0.2 6080 0 || rc=$?
  elapsed=$(awk -v start="$before" -v stop="$EPOCHREALTIME" 'BEGIN { print stop - start }')
  (( rc == 0 )) && [[ "$(cat "$d/privacy-failed")" == unverified ]] &&
    awk -v t="$elapsed" 'BEGIN { exit !(t <= 2.5) }'
}
t 'L-6: slow authentication probes fail closed and finish teardown within 2.5 s' monitor_slow_authenticated_probe

# The real c1_spawn/c1_start_watchdog/c1_cleanup_session paths, with a test
# launcher but no Codespace, network, or Entra. A failed monitor teardown marker
# must stop the launcher and remove the marked session without a manual prompt.
watchdog_teardown_end_to_end() {
  local root="$TMP/l9-real" s pid st i
  s=$(c1_session_new "$root")
  pid=$(c1_spawn "$s" test-launcher bash -c 'trap "exit 0" TERM; while :; do sleep 0.2; done')
  st=$(c1_proc_start "$pid"); [[ -n "$st" ]] || return 1
  printf '%s %s %s\n' "$pid" "$st" "$s" >"$root/active"
  c1_start_watchdog "$root" "$s" "$pid" "$HERE/../lib/c1-lib.sh"
  printf 'now teardown-failed\n' >"$s/privacy-failed"
  for (( i=0; i<100; i++ )); do
    [[ ! -d "$s" && ! -f "$root/active" ]] && break
    sleep 0.1
  done
  [[ ! -d "$s" && ! -f "$root/active" ]] &&
    [[ "$(c1_proc_stat_field "$pid" 1)" =~ ^[ZX]?$ ]]
}
t 'L-9: real watchdog terminates marked launcher and removes session after teardown-failed' watchdog_teardown_end_to_end

t 'L-6: monitor-scoped probe deadline is 0.4s and fast teardown is enabled' bash -c \
  'grep -Fq "local C1_PROBE_MAX_TIME=0.4 C1_MONITOR_FAST_TEARDOWN=1" "$1" &&
   grep -Fq "local wait_steps=20" "$1" &&
   grep -Fq "wait_steps=5" "$1" &&
   grep -Fq -- "--max-time" "$1" && grep -Fq 'C1_PROBE_MAX_TIME' "$1"' _ "$HERE/../lib/c1-lib.sh"

echo '# pinned PR #60 integration (real repository history)'
if [[ -n "${TOP:=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)}" ]] && git -C "$TOP" cat-file -e "$C1_PW_SHA_APPROVED^{commit}" 2>/dev/null \
   && git -C "$TOP" cat-file -e "$C1_AUTH_SHA_APPROVED^{commit}" 2>/dev/null; then
  t 'pinned AUTH is an ancestor of PW and the allowlist equals the real diff exactly (never widened)' \
    c1_verify_commits "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES"
  t 'pinned diff: App.tsx is the only production file and is recorded pending separate review' bash -c \
    'source "$1"; o=$(c1_verify_prod_pending "$2" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES" "$C1_PROD_REVIEW_PENDING"); [[ "$o" =~ ^frontend/src/App\.tsx@[0-9a-f]{40}$ ]]' _ "$HERE/../lib/c1-lib.sh" "$TOP"
  t 'Entra auth modules are byte-identical between the auth baseline and the tested head' \
    git -C "$TOP" diff --quiet "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" -- frontend/src/entraAuth.ts frontend/src/entraAuthCore.ts frontend/package-lock.json
  pinned_vite_guard() {
    local d="$TMP/pinvite" f; mkdir -p "$d/frontend"
    for f in vite.config.ts playwright.config.mjs package.json; do git -C "$TOP" show "$C1_PW_SHA_APPROVED:frontend/$f" >"$d/frontend/$f"; done
    [[ -z "$(git -C "$TOP" ls-tree --name-only "$C1_PW_SHA_APPROVED" frontend/ | grep -E 'vite\.config\.' | grep -v '^frontend/vite.config.ts$')" ]]
    c1_check_vite_host_guard "$d"
  }
  t 'the pinned PR #60 Vite/Playwright configuration passes the 5173 host-guard check (L-11)' pinned_vite_guard
else skip 'pinned PR #60 integration (pinned commits not in local history)'; fi

echo '# harness diff scope after trusted-main workflow reconciliation'
TOP=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)
# This is the separate PR #60 trusted-main *integration* baseline. It is NOT
# the pinned C1 test-suite head and must never widen C1_AUTH_TO_PW_FILES.
C1_PR60_WORKFLOW_BASE='c0cc7254e4154fd15fedf33704ac8510bcec88a6'
if [[ -n "$TOP" ]] && git -C "$TOP" cat-file -e "$C1_PR60_WORKFLOW_BASE^{commit}" 2>/dev/null \
   && git -C "$TOP" merge-base --is-ancestor "$C1_PR60_WORKFLOW_BASE" HEAD 2>/dev/null; then
  t 'harness branch changes only tools/c1-harness/** relative to trusted PR60 workflow base' \
    bash -c '! git -C "$1" diff --name-only "$2" HEAD | grep -v "^tools/c1-harness/"' _ "$TOP" "$C1_PR60_WORKFLOW_BASE"
  t 'approved frontend suite is byte-identical to pinned e187311 suite at HEAD' \
    git -C "$TOP" diff --quiet "$C1_PW_SHA_APPROVED" HEAD -- frontend
  t 'trusted PR60 workflow and backend files are unmodified by PR74 harness' \
    git -C "$TOP" diff --quiet "$C1_PR60_WORKFLOW_BASE" HEAD -- backend scripts tests .github
else skip 'diff scope (trusted workflow base not in local history)'; fi

echo
echo "c1 harness shell tests: $PASSED passed, $FAILED failed, $SKIPPED skipped"
(( FAILED == 0 ))

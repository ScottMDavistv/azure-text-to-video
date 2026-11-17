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
t() { # t NAME CMD...  (expects success)
  local name=$1 rc=0; shift
  set +e; run_case "$@"; rc=$?; set -e
  if (( rc == 0 )); then PASSED=$((PASSED+1)); echo "ok   - $name"
  else FAILED=$((FAILED+1)); echo "FAIL - $name (exit $rc)"; sed 's/^/       /' "$TMP/out" | tail -15; fi
}
f() { # f NAME CMD...  (expects failure, but not a SIGPIPE/crash-style abort)
  local name=$1 rc=0; shift
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

echo '# preflight checks'
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
t 'approved SHAs are pinned constants with no environment override' bash -c 'C1_PW_SHA_APPROVED=bad C1_AUTH_SHA_APPROVED=bad; source "$1"; [[ $C1_PW_SHA_APPROVED == 6b45d0acad10065cb825c12c4544cabdad3441d7 && $C1_AUTH_SHA_APPROVED == 73fe6d2a8aeeef97d34ba23c83fcce5e63f649cc ]]' _ "$HERE/../lib/c1-lib.sh"
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
  t 'authenticated probe reaches a different service (no nonce) -> unverified' decoy_expect wrongport unverified
  t 'wrong token -> authenticated probe never reaches decoy -> unverified' decoy_expect private unverified "$PV/bad.hdr"
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
    monitor() {
      local s ws wsst; s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      c1_start_display_vnc "$s" "$(c1_gen_password)"; ws=$(c1_start_websockify "$s" "$DP"); wsst=$(c1_proc_start "$ws")
      # shellcheck disable=SC2016
      c1_spawn "$s" privacy-monitor bash -c 'source "$1"; c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "$7" 1' \
        _ "$HERE/../lib/c1-lib.sh" "$s" "$BASE" "$PV/hdr" "$ws" "$wsst" "$CA" >/dev/null
      sleep 2.5; alive "$ws"                                # stays up while private
      mode public
      for _ in $(seq 1 40); do [[ -e "$s/privacy-failed" ]] && ! alive "$ws" && break; sleep 0.25; done
      local ok=0; [[ -e "$s/privacy-failed" ]] && ! alive "$ws" && c1_port_free "$DP" && ok=1
      mode private; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor tears noVNC down within seconds of the port turning public' monitor
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
t 'launcher has no typed-confirmation privacy fallback' bash -c '! grep -q "Type PRIVATE" "$1" "$2"' _ "$HERE/../c1-session.sh" "$HERE/../lib/c1-lib.sh"

echo '# harness diff scope'
TOP=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -n "$TOP" ]] && git -C "$TOP" cat-file -e "$C1_PW_SHA_APPROVED^{commit}" 2>/dev/null \
   && git -C "$TOP" merge-base --is-ancestor "$C1_PW_SHA_APPROVED" HEAD 2>/dev/null; then
  t 'harness branch changes only tools/c1-harness/** relative to approved Playwright SHA' \
    bash -c '! git -C "$1" diff --name-only "$2" HEAD | grep -v "^tools/c1-harness/"' _ "$TOP" "$C1_PW_SHA_APPROVED"
  t 'approved suite files are byte-identical at HEAD' \
    git -C "$TOP" diff --quiet "$C1_PW_SHA_APPROVED" HEAD -- frontend backend scripts tests .github
else skip 'diff scope (approved Playwright SHA not in local history)'; fi

echo
echo "c1 harness shell tests: $PASSED passed, $FAILED failed, $SKIPPED skipped"
(( FAILED == 0 ))

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
cleanup_tests() { c1_cleanup_all "$TMP/state" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup_tests EXIT

# Each case runs in a subshell with errexit+pipefail ACTIVE (not inside an
# `if` condition, where bash silently disables errexit), matching the launcher.
run_case() { local rc=0; set +e; ( set -Eeuo pipefail; "$@" ) >"$TMP/out" 2>&1; rc=$?; set -e; return $rc; }
t() { # t NAME CMD...  (expects success)
  local name=$1 rc=0; shift
  run_case "$@" || rc=$?
  if (( rc == 0 )); then PASSED=$((PASSED+1)); echo "ok   - $name"
  else FAILED=$((FAILED+1)); echo "FAIL - $name (exit $rc)"; sed 's/^/       /' "$TMP/out" | tail -15; fi
}
f() { # f NAME CMD...  (expects failure, but not a SIGPIPE/crash-style abort)
  local name=$1 rc=0; shift
  run_case "$@" || rc=$?
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
t 'x11vnc: no TCP port, 0600 unix socket, one-time password file, no -nopw/-passwd' bash -c 'source "$1"; c=$(c1_cmd_x11vnc :99 "$2" | tr "\n" " "); [[ "$c" == *"-rfbport 0"* && "$c" == *"-unixsock $2/vnc.sock"* && "$c" == *"-passwdfile rm:$2/vncpass"* && "$c" == *"-localhost"* && "$c" != *"-nopw"* && "$c" != *"-passwd "* && "$c" != *"-shared"* ]]' _ "$HERE/../lib/c1-lib.sh" "$S0"
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
    ! grep -q "@/tmp/.X11-unix/X$d" /proc/net/unix             # no abstract X socket
    # shellcheck disable=SC2046  # pid list is intentionally word-split
    [[ "$(c1_listeners_for_pids $(c1_marked_pids "$s"))" == "127.0.0.1:$port" ]]
    # RFB handshake over the VNC socket must offer ONLY VNC password auth (type 2).
    python3 - "$s/vnc.sock" <<'PY'
import socket, sys
c = socket.socket(socket.AF_UNIX); c.connect(sys.argv[1]); c.settimeout(5)
ver = c.recv(12); assert ver.startswith(b'RFB '), ver
c.sendall(b'RFB 003.008\n'); n = c.recv(1)[0]; types = list(c.recv(n))
assert n > 0 and types == [2], ('security types', types)
PY
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

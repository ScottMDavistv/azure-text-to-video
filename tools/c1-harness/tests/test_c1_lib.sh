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

prod_pending_fixture() {   # App.tsx (production) is allowlisted between AUTH and PROD; App.tsx must then be pending review or certified
  local exp2=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx' out
  "${G[@]}" -C "$R" checkout -q -b prodfix "$PW"
  echo 'app v2' >"$R/frontend/src/App.tsx"; "${G[@]}" -C "$R" commit -qam prod
  local prod; prod=$(git -C "$R" rev-parse HEAD)
  c1_verify_commits "$R" "$AUTH" "$prod" "$exp2"
  out=$(c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" 'frontend/src/App.tsx' '')
  [[ "$out" == "frontend/src/App.tsx@$(git -C "$R" rev-parse "$prod:frontend/src/App.tsx")" ]]
  if c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" '' ''; then echo 'unlisted production file accepted'; return 1; fi
  if c1_verify_prod_pending "$R" "$AUTH" "$prod" "$exp2" $'frontend/src/App.tsx\nfrontend/src/entraAuth.ts' ''; then echo 'pending file outside allowlist accepted'; return 1; fi
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$PW" "$EXP" '' '')" == none ]]
  "${G[@]}" -C "$R" checkout -q other
}
t 'uncertified production files in the approved diff must be recorded as pending review, with exact blob SHA' prod_pending_fixture
prod_pending_unchanged() {  # a pending path that does not differ AUTH->PW is rejected (keeps the list honest)
  local exp2=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx'
  ! c1_verify_prod_pending "$R" "$AUTH" "$PW" "$exp2" 'frontend/src/App.tsx' ''
}
t 'a pending-review path that is unchanged between AUTH and PW is rejected' prod_pending_unchanged

# ---- R13 certified-production register (B-1 correction) ----
# Fixture: CERT changes production App.tsx (v2) and adds production newProd.ts;
# CERT3 changes App.tsx again (v3) after "certification". Every negative case
# below first proves the matching VALID configuration passes on the same
# fixture, then requires rc=1 AND the specific rejection message, so a case can
# never pass because of an unrelated environment or git failure.
"${G[@]}" -C "$R" checkout -q -b certfix "$PW"
echo 'app v2' >"$R/frontend/src/App.tsx"; echo 'export const p = 1' >"$R/frontend/src/newProd.ts"
"${G[@]}" -C "$R" add -A && "${G[@]}" -C "$R" commit -qm cert
CERT=$(git -C "$R" rev-parse HEAD)
echo 'app v3' >"$R/frontend/src/App.tsx"; "${G[@]}" -C "$R" commit -qam cert3
CERT3=$(git -C "$R" rev-parse HEAD)
"${G[@]}" -C "$R" checkout -q other
CEXP=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx\nfrontend/src/newProd.ts'
APPB=$(git -C "$R" rev-parse "$CERT:frontend/src/App.tsx")
NEWB=$(git -C "$R" rev-parse "$CERT:frontend/src/newProd.ts")
AUTHAPPB=$(git -C "$R" rev-parse "$AUTH:frontend/src/App.tsx")
CERTOK="frontend/src/App.tsx@$APPB"$'\n'"frontend/src/newProd.ts@$NEWB"
# r13_rejects REGEX ARGS... -> 0 only if c1_verify_prod_pending ARGS returns 1,
# prints nothing on stdout, and its stderr matches REGEX.
r13_rejects() {
  local re=$1 rc=0 out; shift
  out=$(c1_verify_prod_pending "$@" 2>"$TMP/r13err") || rc=$?
  (( rc == 1 )) || { echo "expected rc=1, got rc=$rc"; return 1; }
  [[ -z "$out" ]] || { echo "unexpected stdout: $out"; return 1; }
  grep -qE -- "$re" "$TMP/r13err" || { echo "stderr did not match /$re/:"; cat "$TMP/r13err"; return 1; }
}
r13_cert_valid() {
  c1_verify_commits "$R" "$AUTH" "$CERT" "$CEXP"
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK" 2>"$TMP/r13log")" == none ]]
  grep -qF "production file frontend/src/App.tsx certified at exact blob $APPB" "$TMP/r13log"
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$CERT" "$CEXP" 'frontend/src/newProd.ts' "frontend/src/App.tsx@$APPB")" == "frontend/src/newProd.ts@$NEWB" ]]
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$CERT" "$CEXP" $'\nfrontend/src/newProd.ts\n' $'\n'"frontend/src/App.tsx@$APPB"$'\n')" == "frontend/src/newProd.ts@$NEWB" ]]
}
t 'R13 certified: exact-blob certified production files PASS (none pending); pending + certified mix records only the pending blob' r13_cert_valid
r13_mismatch() {
  r13_cert_valid
  r13_rejects 'certified file frontend/src/App\.tsx blob .* does not match PW blob' "$R" "$AUTH" "$CERT" "$CEXP" '' "frontend/src/App.tsx@$AUTHAPPB"$'\n'"frontend/src/newProd.ts@$NEWB"
  r13_rejects 'certified file frontend/src/App\.tsx blob 000000000000 does not match' "$R" "$AUTH" "$CERT" "$CEXP" '' "frontend/src/App.tsx@$(printf '0%.0s' {1..40})"$'\n'"frontend/src/newProd.ts@$NEWB"
  r13_rejects 'certified file frontend/src/newProd\.ts blob .* does not match' "$R" "$AUTH" "$CERT" "$CEXP" '' "frontend/src/App.tsx@$APPB"$'\n'"frontend/src/newProd.ts@$APPB"
}
t 'R13 certified: a certified blob that does not match the blob at PW is rejected' r13_mismatch
r13_changed_after_cert() {  # App.tsx changed again after certification -> the old certification no longer clears it
  [[ "$(c1_verify_prod_pending "$R" "$AUTH" "$CERT3" "$CEXP" 'frontend/src/App.tsx' "frontend/src/newProd.ts@$NEWB")" == "frontend/src/App.tsx@$(git -C "$R" rev-parse "$CERT3:frontend/src/App.tsx")" ]]
  r13_rejects 'certified file frontend/src/App\.tsx blob .* re-certification required' "$R" "$AUTH" "$CERT3" "$CEXP" '' "$CERTOK"
}
t 'R13 certified: a production file modified after certification fails closed until re-certified' r13_changed_after_cert
r13_malformed() {
  r13_cert_valid
  local bad
  for bad in 'frontend/src/App.tsx' "frontend/src/App.tsx@${APPB:0:12}" "frontend/src/App.tsx@${APPB^^}" "frontend/src/App.tsx@${APPB}0" \
             "frontend/src/App.tsx@$APPB " " frontend/src/App.tsx@$APPB" "frontend/src/App.tsx @$APPB" "frontend/src/App.tsx@$APPB@$APPB" \
             "frontend/src/App.tsx,frontend/src/newProd.ts@$NEWB" "-frontend/src/App.tsx@$APPB" "frontend/src/App.tsx"$'\r'"@$APPB" \
             "frontend/src/App.tsx@$APPB"$'\r' "@$APPB"; do
    r13_rejects 'malformed certified entry' "$R" "$AUTH" "$CERT" "$CEXP" '' "$bad"$'\n'"frontend/src/newProd.ts@$NEWB" || { echo "case: $(printf %q "$bad")"; return 1; }
  done
  for bad in "frontend/src/../src/App.tsx@$APPB" "frontend//src/App.tsx@$APPB" "frontend/src/@$APPB"; do
    r13_rejects 'malformed certified entry' "$R" "$AUTH" "$CERT" "$CEXP" '' "$bad"$'\n'"frontend/src/newProd.ts@$NEWB" || { echo "case: $(printf %q "$bad")"; return 1; }
  done
  for bad in "frontend/src/App.tsx@$APPB" 'frontend/src/App.tsx ' '-frontend/src/App.tsx' 'frontend/src/../src/App.tsx'; do
    r13_rejects 'malformed pending-review entry' "$R" "$AUTH" "$CERT" "$CEXP" "$bad" "frontend/src/newProd.ts@$NEWB" || { echo "case: $(printf %q "$bad")"; return 1; }
  done
}
t 'R13 certified: malformed certified and pending entries are rejected' r13_malformed
r13_unknown() {
  r13_cert_valid
  local eb; eb=$(git -C "$R" rev-parse "$CERT:frontend/src/entraAuth.ts")
  r13_rejects 'certified file frontend/src/entraAuth\.ts is not in the approved diff' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK"$'\n'"frontend/src/entraAuth.ts@$eb"
  r13_rejects 'certified file frontend/src/ghost\.ts is not in the approved diff' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK"$'\n'"frontend/src/ghost.ts@$APPB"
  r13_rejects 'pending-review file frontend/src/entraAuth\.ts is not in the approved diff' "$R" "$AUTH" "$CERT" "$CEXP" 'frontend/src/entraAuth.ts' "$CERTOK"
  r13_rejects 'certified file frontend/playwright\.config\.mjs is test infrastructure' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK"$'\n'"frontend/playwright.config.mjs@$(git -C "$R" rev-parse "$CERT:frontend/playwright.config.mjs")"
  r13_rejects 'pending-review file frontend/e2e/entra-c1\.spec\.mjs is test infrastructure' "$R" "$AUTH" "$CERT" "$CEXP" 'frontend/e2e/entra-c1.spec.mjs' "$CERTOK"
}
t 'R13 certified: unknown, out-of-diff and test-infrastructure entries are rejected' r13_unknown
r13_duplicate() {
  r13_cert_valid
  r13_rejects 'frontend/src/App\.tsx is listed more than once \(certified and certified\)' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK"$'\n'"frontend/src/App.tsx@$APPB"
  r13_rejects 'frontend/src/App\.tsx is listed more than once \(certified and certified\)' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK"$'\n'"frontend/src/App.tsx@$AUTHAPPB"
  r13_rejects 'frontend/src/newProd\.ts is listed more than once \(pending-review and pending-review\)' "$R" "$AUTH" "$CERT" "$CEXP" $'frontend/src/newProd.ts\nfrontend/src/newProd.ts' "frontend/src/App.tsx@$APPB"
  r13_rejects 'frontend/src/App\.tsx is listed more than once \(pending-review and certified\)' "$R" "$AUTH" "$CERT" "$CEXP" 'frontend/src/App.tsx' "$CERTOK"
}
t 'R13 certified: duplicate and overlapping pending/certified entries are rejected' r13_duplicate
r13_unchanged() {   # PW where App.tsx is unchanged vs AUTH; allowlist widened only for this negative case
  local exp2=$'frontend/e2e/entra-c1.spec.mjs\nfrontend/playwright.config.mjs\nfrontend/src/App.tsx'
  r13_rejects 'certified file frontend/src/App\.tsx does not differ between AUTH and PW' "$R" "$AUTH" "$PW" "$exp2" '' "frontend/src/App.tsx@$AUTHAPPB"
}
t 'R13 certified: a certified path that is unchanged between AUTH and PW is rejected' r13_unchanged
r13_uncleared() {
  r13_cert_valid
  r13_rejects 'production file frontend/src/App\.tsx is allowlisted but neither pending independent review nor certified' "$R" "$AUTH" "$CERT" "$CEXP" '' "frontend/src/newProd.ts@$NEWB"
  r13_rejects 'production file frontend/src/newProd\.ts is allowlisted but neither pending' "$R" "$AUTH" "$CERT" "$CEXP" '' "frontend/src/App.tsx@$APPB"
  r13_rejects 'production file frontend/src/App\.tsx is allowlisted but neither' "$R" "$AUTH" "$CERT" "$CEXP" '' ''
}
t 'R13 certified: changed or newly added production code lacking clearance fails closed' r13_uncleared
r13_args_and_git_errors() {
  r13_cert_valid
  r13_rejects 'requires REPO AUTH PW ALLOWED PENDING CERTIFIED' "$R" "$AUTH" "$CERT" "$CEXP" ''
  r13_rejects 'requires REPO AUTH PW ALLOWED PENDING CERTIFIED' "$R" "$AUTH" "$CERT" "$CEXP" '' "$CERTOK" extra
  r13_rejects 'PW must be a full 40-hex SHA' "$R" "$AUTH" "${CERT:0:12}" "$CEXP" '' "$CERTOK"
  r13_rejects 'cannot compare certified file frontend/src/App\.tsx between AUTH and PW \(git rc' "$R" "$(printf 'f%.0s' {1..40})" "$CERT" "$CEXP" '' "$CERTOK"
}
t 'R13 certified: missing arguments and git comparison errors fail closed (never read as "differs")' r13_args_and_git_errors
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
t 'x11vnc: no IPv4 or IPv6 TCP port (-rfbport 0 -rfbportv6 0 -no6, H-1), 0600 unix socket, one-time password file, no -nopw/-passwd' bash -c 'source "$1"; c=$(c1_cmd_x11vnc :99 "$2" | tr "\n" " "); [[ "$c" == *"-rfbport 0 "* && "$c" == *"-rfbportv6 0 "* && "$c" == *" -no6 "* && "$c" == *"-unixsock $2/vnc.sock"* && "$c" == *"-passwdfile rm:$2/vncpass"* && "$c" != *"-localhost"* && "$c" != *"-nopw"* && "$c" != *"-passwd "* && "$c" != *"-shared"* ]]' _ "$HERE/../lib/c1-lib.sh" "$S0"
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
t 'approved SHAs are pinned constants with no environment override' bash -c 'C1_PW_SHA_APPROVED=bad C1_AUTH_SHA_APPROVED=bad; source "$1"; [[ $C1_PW_SHA_APPROVED == f501bf5b572fe55bcfb35511eb778c0b2bd97d53 && $C1_AUTH_SHA_APPROVED == c46e2e8c67ee5a2668d113ceef15c5a4774cde69 ]]' _ "$HERE/../lib/c1-lib.sh"
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
reuse_with_start() {   # a recorded PID/starttime matches an UNRELATED process (no marker)
  local s v rc=0 reg; s=$(c1_session_new "$ROOT")
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.3
  printf '%s %s\n' "$v" "$(c1_proc_start "$v")" >"$s/pids"; printf '999999 1 %s\n' "$s" >"$ROOT/active"
  # No unrelated process may be signalled. The evidence must remain unresolved
  # while this PID/starttime pair is live (fail closed, L-3).
  c1_cleanup_all "$ROOT" 2>/dev/null || rc=$?
  reg="$ROOT/.${s##*/}.pids"
  local ok=0
  (( rc != 0 )) && alive "$v" && [[ -s "$reg" ]] && ok=1
  # The fixture owns this foreign process, so only the fixture may stop it.
  kill "$v" 2>/dev/null || true
  wait "$v" 2>/dev/null || true
  # Once gone, strict recovery must be able to verify/retire the saved record.
  (( ok == 1 )) && c1_cleanup_all "$ROOT" && [[ ! -e "$reg" ]]
}
t 'stale recovery never signals an unrelated process (or its group) that reused a recorded PID' reuse_with_start
reuse_legacy_line() {  # legacy/garbage pids lines must not signal or erase evidence
  local s v rc=0 reg; s=$(c1_session_new "$ROOT")
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.3
  printf '%s\nnot-a-pid 5\n-1 1\n' "$v" >"$s/pids"; printf '999999 1 %s\n' "$s" >"$ROOT/active"
  c1_cleanup_all "$ROOT" 2>/dev/null || rc=$?
  reg="$ROOT/.${s##*/}.pids"
  local ok=0
  (( rc != 0 )) && [[ -s "$reg" ]] && alive "$v" && ok=1
  kill "$v" 2>/dev/null || true
  wait "$v" 2>/dev/null || true
  # Only the test fixture may remove deliberately corrupt evidence.
  (( ok == 1 )) || return 1
  rm -f -- "$reg"
  c1_cleanup_all "$ROOT"
}
t 'stale recovery ignores pids lines without a verified start time' reuse_legacy_line

recovery_refuses_unresolved() {  # active was deleted before a failed verify
  local s v reg; s=$(c1_session_new "$ROOT")
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.2
  printf '%s %s\n' "$v" "$(c1_proc_start "$v")" >"$s/pids"
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.${s##*/}.pids"
  [[ ! -e "$ROOT/active" && -s "$reg" ]]
  c1_verify_cleanup "$s" 2>/dev/null && return 1
  c1_recover_before_start "$ROOT" 2>/dev/null && return 1
  # The same evidence must gate a new session even without an active lock.
  [[ -s "$reg" ]] && alive "$v" || return 1
  kill "$v" 2>/dev/null || true
  wait "$v" 2>/dev/null || true
  c1_recover_before_start "$ROOT" && [[ ! -e "$reg" ]]
}
t 'N-1: orphan survivor evidence refuses next session until independently retired' recovery_refuses_unresolved

recovery_refuses_orphan_inode() {
  local s reg; s=$(c1_session_new "$ROOT")
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.${s##*/}.inodes"
  printf '999999999\n' >"$reg"
  # An unresolvable evidence reader must not convert this into a clean start.
  C1_SS_BIN=/nonexistent/ss c1_recover_before_start "$ROOT" 2>/dev/null && return 1
  [[ -s "$reg" ]] || return 1
  c1_recover_before_start "$ROOT" && [[ ! -e "$reg" ]]
}
t 'N-1: orphan listener evidence is verified before next session' recovery_refuses_orphan_inode

pid_preserve_denied() {
  local s rc=0 archive; s=$(c1_session_new "$ROOT")
  printf '123456789 1\n' >"$s/pids"
  archive="$ROOT/.${s##*/}.pids"
  mkdir "$archive"  # an archive directory must not be mistaken for a successful write
  c1_cleanup_session "$ROOT" "$s" 2>/dev/null || rc=$?
  (( rc != 0 )) && [[ -d "$s" && -s "$s/pids" && -d "$archive" ]] || return 1
  rmdir "$archive"
  c1_cleanup_session "$ROOT" "$s" && c1_verify_cleanup "$s"
}
t 'N-2: failed PID archive preservation retains source evidence and fails closed' pid_preserve_denied

malformed_preserved_pid() {
  local s reg rc=0; s=$(c1_session_new "$ROOT")
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.${s##*/}.pids"
  printf '123456789\nnot-a-pid 1\n123 45 extra\n' >"$reg"
  c1_verify_cleanup "$s" 2>/dev/null || rc=$?
  (( rc != 0 )) && [[ -s "$reg" ]] || return 1
  c1_recover_before_start "$ROOT" 2>/dev/null && return 1
  [[ -s "$reg" ]] || return 1
  # Test teardown explicitly removes manufactured invalid bytes.
  rm -f -- "$reg"
  c1_recover_before_start "$ROOT"
}
t 'N-4: malformed preserved PID lines remain unresolved across recovery' malformed_preserved_pid
# N-8: dangling archived evidence must be inspected, retained, and must
# prevent both cleanup and the next session, even though -e returns false.
dangling_archive_recovery() {
  local kind=$1 s reg rc=0; s=$(c1_session_new "$ROOT")
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.${s##*/}.$kind"
  ln -s "$s/never-created" "$reg"
  [[ -L "$reg" && ! -e "$reg" ]] || return 1
  c1_verify_cleanup "$s" >/dev/null 2>&1 || rc=$?
  (( rc != 0 )) && [[ -L "$reg" ]] || return 1
  rc=0; c1_cleanup_all "$ROOT" >/dev/null 2>&1 || rc=$?
  (( rc != 0 )) && [[ -L "$reg" ]] || return 1
  rc=0; c1_recover_before_start "$ROOT" >/dev/null 2>&1 || rc=$?
  (( rc != 0 )) && [[ -L "$reg" && ! -e "$reg" ]] || return 1
  rm -f -- "$reg"  # retire only this test's dangling symlink
  c1_recover_before_start "$ROOT"
}
t 'N-8: dangling .pids evidence blocks cleanup and recovery without deletion' dangling_archive_recovery pids
t 'N-8: dangling .inodes evidence blocks cleanup and recovery without deletion' dangling_archive_recovery inodes

# Negative control: restore the vulnerable -e-only scan in an isolated copy.
# Stub only verification to isolate scan sensitivity, not the mutation itself.
n8_scan_negative_control() {
  local kind=$1 d="$TMP/n8-mutant-$1" src="$HERE/../lib/c1-lib.sh"
  mkdir -p "$d"
  python3 -I - "$src" "$d/lib.sh" <<'PY'
import pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text()
old = '    [[ -e "$record" || -L "$record" ]] || continue'
new = '    [[ -e "$record" ]] || continue'
assert source.count(old) == 1, 'missing exact N-8 scan guard'
pathlib.Path(sys.argv[2]).write_text(source.replace(old, new))
PY
  ln -s "$d/missing-target" "$d/.session.n8.$kind"
  [[ -L "$d/.session.n8.$kind" && ! -e "$d/.session.n8.$kind" ]] || return 1
  local runner='source "$1"
c1_verify_cleanup() { return 1; }
c1_cleanup_all "$2"'
  local rc=0
  bash -c "$runner" _ "$src" "$d" >/dev/null 2>&1 || rc=$?
  (( rc != 0 )) || return 1  # fixed scan must inspect the unresolved link
  rc=0
  bash -c "$runner" _ "$d/lib.sh" "$d" >/dev/null 2>&1 || rc=$?
  (( rc == 0 )) && [[ -L "$d/.session.n8.$kind" ]]
}
t 'N-8 negative control: -e-only scan misses dangling .pids evidence' n8_scan_negative_control pids
t 'N-8 negative control: -e-only scan misses dangling .inodes evidence' n8_scan_negative_control inodes

n10_pid_archive() (
  local mode=$1 root="$TMP/n10-$1" s reg target
  mkdir -p "$root"
  s="$root/session.n10"
  reg="$root/.session.n10.pids"
  target="$root/pid-target"
  if [[ $mode == empty ]]; then
    : >"$reg"
  else
    printf '123456789 1\n' >"$target"
    ln -s "$target" "$reg"
  fi
  if c1_verify_cleanup "$s" || c1_recover_before_start "$root"; then return 1; fi
  if [[ $mode == empty ]]; then
    [[ -f "$reg" && ! -s "$reg" ]]
  else
    [[ -L "$reg" && $(cat "$target") == '123456789 1' ]]
  fi
)
n10_pid_archive_mutation() (
  local original mutated guard='! -f "$preg" || -L "$preg" || ! -r "$preg" || ! -s "$preg"'
  original=$(declare -f c1_verify_cleanup)
  mutated=${original/"$guard"/'! -f "$preg" || ! -r "$preg"'}
  [[ $mutated != "$original" ]] || return 1
  eval "$mutated"
  ! n10_pid_archive empty && ! n10_pid_archive symlink
)
t 'N-10: empty PID archive blocks recovery and is retained' n10_pid_archive empty
t 'N-10: symlinked PID archive blocks recovery even with a valid target' n10_pid_archive symlink
t 'N-10: archive tests detect removal of the empty-file and symlink guards' n10_pid_archive_mutation

n10_pid_identity() (
  local root="$TMP/n10-identity" s reg child
  mkdir -p "$root"; s="$root/session.n10"; reg="$root/.session.n10.pids"
  sleep 60 & child=$!
  trap 'kill "$child" 2>/dev/null || true' EXIT
  printf '%s 1\n' "$child" >"$reg"
  c1_proc_start() { return 1; }
  if c1_verify_cleanup "$s"; then return 1; fi
  [[ -s "$reg" ]]
  kill "$child"; wait "$child" 2>/dev/null || true
  trap - EXIT
  c1_verify_cleanup "$s"
  [[ ! -e "$reg" ]]
)
t 'N-10: unreadable live PID identity is retained and a retired valid record is removed' n10_pid_identity

# N-6: malformed or empty inode archives must block and survive recovery.
invalid_preserved_inode() {
  local payload=$1 s reg rc=0; s=$(c1_session_new "$ROOT")
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.$(basename "$s").inodes"
  printf "%s" "$payload" >"$reg"
  c1_verify_cleanup "$s" 2>/dev/null || rc=$?
  (( rc != 0 )) && [[ -f "$reg" ]] || return 1
  c1_recover_before_start "$ROOT" 2>/dev/null && return 1
  [[ -f "$reg" ]] || return 1
  rm -f -- "$reg"  # retire only the test fixture archive
  c1_recover_before_start "$ROOT"
}
t "N-6: malformed inode archive fails closed" invalid_preserved_inode "garbage"
t "N-6: zero inode archive fails closed" invalid_preserved_inode "0"
t "N-6: unexpected extra fields fail closed" invalid_preserved_inode "123 456"
t "N-6: empty inode archive fails closed" invalid_preserved_inode ""

symlink_preserved_inode() {
  local s reg target rc=0; s=$(c1_session_new "$ROOT")
  c1_cleanup_session "$ROOT" "$s"
  reg="$ROOT/.$(basename "$s").inodes"; target="$ROOT/inode-test-target"
  printf "123456789\n" >"$target"
  ln -s "$target" "$reg"
  c1_verify_cleanup "$s" 2>/dev/null || rc=$?
  (( rc != 0 )) && [[ -L "$reg" && -s "$target" ]] || return 1
  rm -f -- "$reg" "$target"
  c1_recover_before_start "$ROOT"
}
t "N-6: symlinked inode archive rejected without affecting target" symlink_preserved_inode
# N-6/N-7: execute the actual cleanup logic with real read permission
# denial. Stub unrelated port/listener checks to isolate the archive guard.
unreadable_archive_probe() {
  local library=$1 kind=$2 d s reg rc=0 runner
  d=$(mktemp -d /tmp/c1-unread-archive.XXXXXX) || return 1
  chmod 777 "$d"
  cp -- "$library" "$d/lib.sh"; chmod 644 "$d/lib.sh"
  s="$d/session.unreadable"; reg="$d/.session.unreadable.$kind"
  if [[ "$kind" == pids ]]; then printf '123456789 1\n' >"$reg"
  else printf '123456789\n' >"$reg"; fi
  chmod 000 "$reg"
  runner='source "$1"
c1_marked_pids() { :; }
c1_port_free() { return 0; }
c1_listen_tables() { C1_LT_PROC=""; C1_LT_SS=""; return 0; }
rc=0
c1_verify_cleanup "$2" >/dev/null 2>&1 || rc=$?
(( rc != 0 )) && [[ -e "$3" ]]'
  if (( EUID == 0 )); then
    if command -v setpriv >/dev/null; then
      setpriv --reuid=65534 --regid=65534 --clear-groups bash -c "$runner" _ "$d/lib.sh" "$s" "$reg" || rc=$?
    else
      rm -rf -- "$d"
      return 2
    fi
  else
    bash -c "$runner" _ "$d/lib.sh" "$s" "$reg" || rc=$?
  fi
  chmod 600 "$reg" 2>/dev/null || true
  rm -rf -- "$d"
  (( rc == 0 ))
}
if (( EUID != 0 )) || command -v setpriv >/dev/null; then
  t 'N-6: unreadable inode archive fails closed and is retained (real EACCES)' \
    unreadable_archive_probe "$HERE/../lib/c1-lib.sh" inodes
  t 'N-7: unreadable PID archive fails closed and is retained (real EACCES)' \
    unreadable_archive_probe "$HERE/../lib/c1-lib.sh" pids
  unreadable_pid_guard_mutant_caught() {
    local d="$TMP/n7-mutant" source_lib="$HERE/../lib/c1-lib.sh"
    sed 's/if \[\[ ! -f "\$preg" || -L "\$preg" || ! -r "\$preg" || ! -s "\$preg" \]\]; then/if false; then/' \
      "$source_lib" >"$d"
    ! cmp -s "$source_lib" "$d" || return 1
    ! unreadable_archive_probe "$d" pids
  }
  t 'N-7 negative control: disabled PID readability guard is detected' unreadable_pid_guard_mutant_caught
else
  skip 'N-6/N-7 unreadable archive EACCES tests (root requires setpriv)'
fi



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
# ---- L-A: persistent subreaper supervision ----------------------------------
# Every c1_spawn runs under lib/c1-supervisor.py, a child subreaper that lives
# until no descendant remains. INVARIANT checked by every case below: cleanup
# plus verification may report success only if no descendant survives.
la_wait() { local i; for (( i=0; i<80; i++ )); do eval "$1" && return 0; sleep 0.05; done; return 1; }
la_sup() { awk 'NR==1 {print $1}' "$1/supervisors"; }
# la_case MODE -> 0 only if MODE's expected outcome holds AND the invariant does.
la_case() (
  local mode=$1 s leader child cst sup crc=0 vrc=0
  s=$(c1_session_new "$ROOT")
  if [[ $mode == intermediate ]]; then   # leader -> A -> scrubbed setsid B; A exits at once
    leader=$(c1_spawn "$s" la bash -c 'bash -c "env -i setsid sleep 300 & echo \$! >\"\$1/child\"" _ "$1"; exec sleep 300' _ "$s")
  else
    leader=$(c1_spawn "$s" la bash -c 'env -i setsid sleep 300 & echo $! >"$1/child"; wait' _ "$s")
  fi
  la_wait '[[ -s "$s/child" ]]'
  child=$(cat "$s/child"); cst=$(c1_proc_start "$child"); sup=$(la_sup "$s")
  trap '[[ "$(c1_proc_start "$child")" != "$cst" ]] || kill -KILL "$child" 2>/dev/null || true' EXIT
  [[ -n "$cst" && -n "$sup" && "$sup" != "$leader" ]]
  la_wait '[[ $(c1_proc_stat_field "$child" 4) == "$child" ]]'   # own setsid session
  if c1_pid_has_marker "$child" "$s"; then return 1; fi          # env-scrubbed
  case $mode in
    orphan-after-scan)  c1_harness_listeners "$s" >/dev/null; kill -KILL "$leader" ;;
    orphan-before-scan) [[ ! -e "$s/descendants" ]]; kill -KILL "$leader" ;;   # no scan ever saw it
    supervisor-killed)  [[ ! -e "$s/descendants" ]]; kill -KILL "$sup" "$leader" ;;
  esac
  local adopted=1
  case $mode in
    orphan-*|intermediate)                                       # adopted by the LIVE supervisor, not PID 1
      la_wait '[[ $(c1_proc_stat_field "$child" 2) == "$sup" ]]' || { adopted=0; echo "not adopted: child ppid $(c1_proc_stat_field "$child" 2), supervisor $sup"; }
      [[ ! -e "$s/descendants" || $mode == orphan-after-scan ]] ;;
    supervisor-killed) la_wait '[[ $(c1_proc_stat_field "$child" 2) != "$leader" ]]' ;;
  esac
  c1_cleanup_session "$ROOT" "$s" 2>/dev/null || crc=$?
  c1_verify_cleanup "$s" 2>/dev/null || vrc=$?
  if (( crc == 0 && vrc == 0 )) && [[ "$(c1_proc_start "$child")" == "$cst" ]]; then
    echo "FALSE SUCCESS: cleanup verified while descendant $child survives"; return 1
  fi
  if [[ $mode == supervisor-killed ]]; then
    # Escaped to PID 1: never a success, and still not one after the child exits,
    # because nothing proves no OTHER descendant escaped with it.
    (( vrc != 0 )) && [[ "$(c1_proc_start "$child")" == "$cst" ]] || { echo "crc=$crc vrc=$vrc"; return 1; }
    kill -KILL "$child"; la_wait '[[ "$(c1_proc_start "$child")" != "$cst" ]]'
    if c1_verify_cleanup "$s" 2>/dev/null; then echo 'unproven supervisor exit accepted'; return 1; fi
    [[ -s "$ROOT/.${s##*/}.supervisors" ]]
    rm -f -- "$ROOT/.${s##*/}.supervisors" "$ROOT/.${s##*/}.reaped" "$ROOT/.${s##*/}.pids"   # fixture-owned evidence
    return 0
  fi
  (( crc == 0 && vrc == 0 && adopted == 1 )) || { echo "crc=$crc vrc=$vrc adopted=$adopted"; return 1; }
  [[ "$(c1_proc_start "$child")" != "$cst" ]]
)
t 'L-A: original case: cleanup signals an attributed env-scrubbed setsid child' la_case attached
t 'L-A: leader killed after a scan: child adopted by the supervisor and removed' la_case orphan-after-scan
t 'L-A: orphan before the first scan: adopted by the live supervisor, removed, verified' la_case orphan-before-scan
t 'L-A: intermediate ancestor exits before any scan: descendant adopted and removed' la_case intermediate
t 'L-A: supervisor SIGKILLed so a descendant escapes to PID 1: verification fails closed' la_case supervisor-killed

la_mutant() (   # la_mutant SED_EXPR MODE -> 0 only if the mutant supervisor yields a detected FALSE SUCCESS
  local m="$TMP/la-mutant.$BASHPID.py" out rc=0
  sed -e "$1" "$C1_SUPERVISOR" >"$m"; ! cmp -s "$m" "$C1_SUPERVISOR"
  C1_SUPERVISOR=$m
  out=$(la_case "$2" 2>&1) || rc=$?
  rm -f -- "$m"
  (( rc != 0 )) && [[ "$out" == *'FALSE SUCCESS'* ]]
)
t 'L-A mutation: supervisor without PR_SET_CHILD_SUBREAPER (orphan before scan) is caught' \
  la_mutant 's/libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0/False/' orphan-before-scan
t 'L-A mutation: supervisor without PR_SET_CHILD_SUBREAPER (intermediate exit) is caught' \
  la_mutant 's/libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0/False/' intermediate
t 'L-A mutation: supervisor that stops when its own child exits (before ECHILD) is caught' \
  la_mutant 's/^\(            rc = 128 - rc if rc < 0 else rc\)$/\1\n            break/' orphan-before-scan
la_mutant_no_proof_check() (
  c1_verify_supervisors() { rm -f -- "$(dirname -- "$1")/.${1##*/}.supervisors"; return 0; }
  local out rc=0; out=$(la_case supervisor-killed 2>&1) || rc=$?
  (( rc != 0 )) && [[ "$out" == *'FALSE SUCCESS'* ]]
)
t 'L-A mutation: verification that ignores missing reaping proofs is caught' la_mutant_no_proof_check

la_failed_signals() (   # every cleanup signal fails: never success, nothing lost; a later cleanup recovers
  local s leader child cst rc=0 vrc=0; s=$(c1_session_new "$ROOT")
  leader=$(c1_spawn "$s" la bash -c 'env -i setsid sleep 300 & echo $! >"$1/child"; wait' _ "$s")
  la_wait '[[ -s "$s/child" ]]'; child=$(cat "$s/child"); cst=$(c1_proc_start "$child")
  trap '[[ "$(c1_proc_start "$child")" != "$cst" ]] || kill -KILL "$child" 2>/dev/null || true' EXIT
  kill -KILL "$leader"; la_wait '[[ $(c1_proc_stat_field "$child" 2) == "$(la_sup "$s")" ]]'
  ( kill() { :; }; C1_SUPERVISOR_WAIT_STEPS=10; c1_cleanup_session "$ROOT" "$s" ) 2>/dev/null || rc=$?
  c1_verify_cleanup "$s" 2>/dev/null || vrc=$?
  (( rc != 0 && vrc != 0 )) && [[ -d "$s" && "$(c1_proc_start "$child")" == "$cst" ]] || { echo "rc=$rc vrc=$vrc"; return 1; }
  c1_cleanup_session "$ROOT" "$s" && c1_verify_cleanup "$s" && [[ "$(c1_proc_start "$child")" != "$cst" ]]
)
t 'L-A: failed signals: cleanup and verification fail, session retained; retry removes the adopted child' la_failed_signals

la_interrupted() (   # cleanup SIGKILLed mid-signal, then the next start's recovery finishes it
  local s leader child cst cp; s=$(c1_session_new "$ROOT")
  # shellcheck disable=SC2016
  leader=$(c1_spawn "$s" la bash -c 'trap "" TERM; env -i setsid "$2" -I -c "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(\"$1/child\",\"w\").write(str(__import__(\"os\").getpid())); time.sleep(300)" & wait' _ "$s" "$PY3_LA")
  la_wait '[[ -s "$s/child" ]]'; child=$(cat "$s/child"); cst=$(c1_proc_start "$child")
  trap '[[ "$(c1_proc_start "$child")" != "$cst" ]] || kill -KILL "$child" 2>/dev/null || true' EXIT
  ( c1_cleanup_session "$ROOT" "$s" ) >/dev/null 2>&1 & cp=$!
  sleep 1; kill -KILL "$cp"; wait "$cp" 2>/dev/null || true    # interrupted inside the TERM grace period
  [[ -d "$s" && "$(c1_proc_start "$child")" == "$cst" ]]
  printf '999999 1 %s\n' "$s" >"$ROOT/active"                   # the launcher died with it
  c1_recover_before_start "$ROOT" 2>/dev/null
  [[ "$(c1_proc_start "$child")" != "$cst" && ! -e "$s" ]] && ! ls "$ROOT"/.${s##*/}.* >/dev/null 2>&1
)
PY3_LA=$(command -v python3)
t 'L-A: interrupted cleanup: recovery removes the TERM-ignoring adopted child and retires all records' la_interrupted

la_supervisor_lifetime() (   # the supervisor outlives every descendant through cleanup
  local s leader child cst sup sst smp; s=$(c1_session_new "$ROOT"); smp="$TMP/la-samples.$BASHPID"
  # shellcheck disable=SC2016
  leader=$(c1_spawn "$s" la bash -c 'env -i setsid "$2" -I -c "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(\"$1/child\",\"w\").write(str(__import__(\"os\").getpid())); time.sleep(300)" & wait' _ "$s" "$PY3_LA")
  la_wait '[[ -s "$s/child" ]]'; child=$(cat "$s/child"); cst=$(c1_proc_start "$child")
  sup=$(la_sup "$s"); sst=$(c1_proc_start "$sup")
  trap '[[ "$(c1_proc_start "$child")" != "$cst" ]] || kill -KILL "$child" 2>/dev/null || true' EXIT
  kill -KILL "$leader"; la_wait '[[ $(c1_proc_stat_field "$child" 2) == "$sup" ]]'   # orphaned, adopted
  ( while :; do   # sample "child-alive supervisor-alive" every 10 ms until both are gone
      c=0 p=0
      [[ "$(c1_proc_start "$child")" == "$cst" && ! "$(c1_proc_stat_field "$child" 1)" =~ ^[ZX]$ ]] && c=1
      [[ "$(c1_proc_start "$sup")" == "$sst" && ! "$(c1_proc_stat_field "$sup" 1)" =~ ^[ZX]$ ]] && p=1
      echo "$c $p"; (( c || p )) || break; sleep 0.01
    done >"$smp" ) & local sampler=$!
  c1_cleanup_session "$ROOT" "$s"; c1_verify_cleanup "$s"
  wait "$sampler"
  # Never a moment where the descendant lived without its supervisor; the
  # supervisor stayed up through the TERM grace period and the KILL escalation.
  ! grep -qx '1 0' "$smp" && [[ $(grep -cx '1 1' "$smp") -ge 50 ]] && tail -1 "$smp" | grep -qx '0 0'
)
t 'L-A: supervisor stays alive through cleanup until its last descendant is reaped (sampled)' la_supervisor_lifetime

la_pid_reuse() (   # supervisor identities whose PID now names an UNRELATED process
  local s v vst rc=0 name=session.la-reuse
  setsid sleep 300 </dev/null >/dev/null 2>&1 & v=$!; sleep 0.2; vst=$(c1_proc_start "$v")
  trap 'kill "$v" 2>/dev/null || true' EXIT
  # (a) recorded supervisor exited with proof; its PID was reused: retire, never signal.
  printf '%s 1\n' "$v" >"$ROOT/.$name.supervisors"; printf '%s 1\n' "$v" >"$ROOT/.$name.reaped"
  c1_verify_cleanup "$ROOT/$name" && alive "$v" && [[ ! -e "$ROOT/.$name.supervisors" && ! -e "$ROOT/.$name.reaped" ]]
  # (b) reused PID, no proof: fails closed, still never signalled.
  printf '%s 1\n' "$v" >"$ROOT/.$name.supervisors"
  if c1_verify_cleanup "$ROOT/$name" 2>/dev/null; then return 1; fi
  alive "$v"; rm -f -- "$ROOT/.$name.supervisors"
  # (c) a live record that matches the unrelated, unmarked process exactly: no signal reaches it.
  s=$(c1_session_new "$ROOT"); printf '%s %s\n' "$v" "$vst" >"$s/supervisors"
  # shellcheck disable=SC2034  # read by c1_cleanup_session
  ( C1_SUPERVISOR_WAIT_STEPS=4; c1_cleanup_session "$ROOT" "$s" ) 2>/dev/null || rc=$?
  (( rc != 0 )) && alive "$v" && [[ -d "$s" ]]
  rm -f -- "$s/supervisors"; c1_cleanup_session "$ROOT" "$s" && c1_verify_cleanup "$s" && alive "$v"
)
t 'L-A: PID reuse: supervisor records never authorize signalling an unrelated process' la_pid_reuse

t 'L-A: the supervisor is the only spawn path (no direct setsid exec in c1_spawn)' bash -c \
  '! grep -Fq "setsid \"\$@\"" "$1" && grep -Fq -e "-I \"\$C1_SUPERVISOR\" \"\$s\"" "$1"' _ "$HERE/../lib/c1-lib.sh"

desktop_environment() (
  local s output
  s=$(c1_session_new "$ROOT")
  trap 'c1_cleanup_session "$ROOT" "$s"' EXIT
  export GITHUB_TOKEN=fixture AZURE_CLIENT_ID=fixture FAL_KEY=fixture ARBITRARY_SECRET=fixture
  export DISPLAY=:123 XAUTHORITY="$s/Xauthority"
  c1_spawn_desktop "$s" desktop-environment env >/dev/null
  for _ in $(seq 1 20); do [[ -s "$s/log/desktop-environment.log" ]] && break; sleep 0.05; done
  output="$s/log/desktop-environment.log"
  grep -Fxq "C1_HARNESS_SESSION=$s" "$output" && grep -Fxq 'DISPLAY=:123' "$output" &&
    grep -Fxq "XAUTHORITY=$XAUTHORITY" "$output" &&
    ! grep -Eq '^(GITHUB_TOKEN|AZURE_CLIENT_ID|FAL_KEY|ARBITRARY_SECRET)=' "$output"
)
desktop_environment_mutation() (
  c1_spawn_desktop() { c1_spawn "$@"; }
  ! desktop_environment
)
t 'L-C: desktop subprocess retains display configuration and drops launcher secrets' desktop_environment
t 'L-C: desktop environment test detects removal of env isolation' desktop_environment_mutation

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
    AZURE_CLIENT_ID=x AWS_ACCESS_KEY_ID=x FAL_KEY=x OPENAI_API_KEY=x CUSTOM_CONFIG=x BASH_ENV=/evil PYTHONPATH=/evil \
    HTTPS_PROXY=http://proxy.invalid:3128 no_proxy=localhost NODE_EXTRA_CA_CERTS=/etc/ssl/proxy.pem SSL_CERT_FILE=/etc/ssl/x.pem
  mapfile -d '' -t e < <(c1_installer_env)
  out=$("${e[@]}" env)
  local v
  for v in GITHUB_TOKEN GH_TOKEN GITHUB_CODESPACE_TOKEN CODESPACE_VSCODE_FOLDER ACTIONS_RUNTIME_TOKEN MY_API_SECRET DB_PASSWORD \
           NODE_OPTIONS NODE_PATH npm_config_registry NPM_CONFIG_USERCONFIG PLAYWRIGHT_DOWNLOAD_HOST PW_X \
           NODE_TLS_REJECT_UNAUTHORIZED __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS VITE_X AZURE_CLIENT_ID AWS_ACCESS_KEY_ID \
           FAL_KEY OPENAI_API_KEY CUSTOM_CONFIG BASH_ENV PYTHONPATH; do
    if grep -q "^$v=" <<<"$out"; then echo "leaked $v"; return 1; fi
  done
  for v in PATH HOME HTTPS_PROXY no_proxy NODE_EXTRA_CA_CERTS SSL_CERT_FILE; do grep -q "^$v=" <<<"$out" || { echo "dropped $v"; return 1; }; done
}
t 'installer environment drops GitHub tokens, secrets and tool overrides; keeps PATH/HOME/proxy/CA trust (I-3)' installerenv
installerenv_mutation() (
  c1_installer_env() { printf '%s\0' env -u GITHUB_TOKEN -u GH_TOKEN; }
  ! installerenv
)
t 'installer environment test detects a denylist regression' installerenv_mutation
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
  export PATH="$bin:$PATH" GITHUB_TOKEN=ghu_fakefakefake GH_TOKEN=x NODE_OPTIONS=--require=/evil MY_SECRET=x FAL_KEY=x
  c1_install_node_deps "$fe" "$cache"
  [[ -s "$TMP/npm.env" && -s "$TMP/npx.env" ]]
  [[ $(grep -c '^PATH=' "$TMP/npm.env") -ge 2 && $(grep -c '^PATH=' "$TMP/npx.env") -ge 2 ]]   # npm ci+install; npx install+install-deps
  ! grep -Eq '^(GITHUB_TOKEN|GH_TOKEN|NODE_OPTIONS|MY_SECRET|FAL_KEY)=' "$TMP/npm.env" "$TMP/npx.env"
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
    # Inode-attributed IPv4+IPv6 evidence: exactly one listener, loopback noVNC, owned by websockify.
    local got; got=$(c1_harness_listeners "$s")
    [[ "$got" =~ ^127\.0\.0\.1:$port\ pid=[0-9]+\ comm=websockify\ ino=[0-9]+$ ]]
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
  # Codespaces browser forwarding currently issues an exact github.dev/pf-signin
  # redirect. That route alone is NOT a privacy proof: the CLI must independently
  # confirm the exact 6080 port is private, and the authenticated nonce must pass.
  pf_signin_redirect_table() {
    local bad
    c1_codespaces_pf_signin_redirect 302 'https://github.dev/pf-signin' || return 1
    c1_codespaces_pf_signin_redirect 302 'https://github.dev/pf-signin?return_to=fixture' || return 1
    for bad in \
      '301 https://github.dev/pf-signin' '307 https://github.dev/pf-signin' \
      '302 http://github.dev/pf-signin' '302 https://github.dev/pf-signin/' \
      '302 https://github.dev/pf-signin/extra' '302 https://github.dev/' \
      '302 https://github.dev/pf-signin#fragment' \
      '302 https://github.dev/pf-signin?x=1#fragment' \
      '302 https://github.dev:443/pf-signin' \
      '302 https://github.dev:8443/pf-signin' \
      '302 https://github.dev@evil.example/pf-signin' \
      '302 https://github.dev.evil.example/pf-signin' \
      '302 https://evil.github.dev/pf-signin' \
      '302 https://github.dev./pf-signin' \
      '302 https://githubXdev/pf-signin' \
      '302 https://github.dev/pf-signin?x=1#bad' \
      '302 https://github.dev/pf-signin?'; do
      local c l
      read -r c l <<<"$bad"
      if c1_codespaces_pf_signin_redirect "$c" "$l"; then
        echo "accepted invalid Codespaces redirect: $bad"; return 1
      fi
    done
  }
  t 'exact Codespaces pf-signin matcher rejects spoofed/ambiguous redirects' pf_signin_redirect_table

  pf_signin_with_gh_readback() (
    local vis=$1 marker_mode=$2 origin_mode=${3:-matching} calls="$PV/pf-calls-$1-$2-${3:-matching}" rc=0
    local mockbin="$TMP/pf-oracle-bin"
    : >"$calls"
    c1_codespace_base() {
      if [[ $origin_mode == matching ]]; then printf '%s' "$BASE"; else printf 'https://other-6080.app.github.dev'; fi
    }
    c1_https_probe() {
      local out=$2 hdr=${3:-}
      if [[ -z "$hdr" ]]; then
        printf '302 https://github.dev/pf-signin?return_to=fixture\n'
      else
        printf 'auth\n' >>"$calls"
        if [[ $marker_mode == valid ]]; then printf 'nonce-marker\n' >"$out"; else printf 'wrong-marker\n' >"$out"; fi
        printf '200 \n'
      fi
    }
    local gh_calls="$PV/pf-gh-$1-$2-${3:-matching}"
    : >"$gh_calls"
    mkdir -p "$mockbin"
    cat >"$mockbin/gh" <<'MOCK_GH'
#!/usr/bin/env bash
[[ "$#" == 6 && "$*" == 'codespace ports -c fixture --json sourcePort,visibility' ]] || exit 3
printf 'gh\n' >>"$GH_CALLS"
if [[ "$GH_VIS" == missing ]]; then exit 1; fi
printf '[{"sourcePort":6080,"visibility":"%s"}]\n' "$GH_VIS"
MOCK_GH
    # Verify the invocation at the executable boundary.
    chmod +x "$mockbin/gh"
    export PATH="$mockbin:$PATH" CODESPACE_NAME=fixture GH_CALLS=$gh_calls GH_VIS=$vis
    c1_privacy_check "$BASE" '/nonce-marker.txt' nonce-marker "$PV/hdr" "$CA" || rc=$?
    if [[ $origin_mode != matching ]]; then
      (( rc != 0 )) && [[ $C1_PRIVACY_STATE == unverified ]] &&
        [[ ! -s "$calls" && ! -s "$gh_calls" ]]
    elif [[ $vis == private && $marker_mode == valid ]]; then
      (( rc == 0 )) && [[ $C1_PRIVACY_STATE == private ]] && [[ $(cat "$calls") == auth && $(cat "$gh_calls") == gh ]]
    elif [[ $vis != private ]]; then
      (( rc != 0 )) && [[ $C1_PRIVACY_STATE == unverified ]] && [[ ! -s "$calls" && $(cat "$gh_calls") == gh ]]
    else
      (( rc != 0 )) && [[ $C1_PRIVACY_STATE == unverified ]] && grep -qx auth "$calls"
    fi
  )
  # Mutate only the loaded function in a subshell: without the production origin
  # guard, the mismatched-origin rejection oracle MUST fail.
  pf_signin_origin_binding_mutation() (
    local original mutated
    original=$(declare -f c1_privacy_check)
    [[ $original == *'unexpected forwarding origin'* ]] || return 1
    mutated=$(printf '%s\n' "$original" |
      awk 'index($0, "if [[ \"$base\" != \"$(c1_codespace_base") && $0 !~ /&&/ { drop=1 }
           drop && /^[[:space:]]*fi;/ { drop=0; next }
           !drop { print }')
    [[ $mutated != "$original" && $mutated != *'unexpected forwarding origin'* ]] || return 1
    eval "$mutated"
    if pf_signin_with_gh_readback private valid mismatched; then
      echo 'origin-binding mutation escaped rejection oracle'
      return 1
    fi
  )
  t 'Codespaces origin-binding test detects removed production guard (mutation control)' pf_signin_origin_binding_mutation
  t 'Codespaces redirect requires GH private readback plus authenticated nonce' pf_signin_with_gh_readback private valid
  t 'Codespaces redirect refuses public GH visibility before token probe' pf_signin_with_gh_readback public valid
  t 'Codespaces redirect refuses org GH visibility before token probe' pf_signin_with_gh_readback org valid
  t 'Codespaces redirect refuses unavailable GH visibility before token probe' pf_signin_with_gh_readback missing valid
  t 'Codespaces redirect refuses mismatched forwarding origin before token probe' pf_signin_with_gh_readback private valid mismatched
  t 'Codespaces redirect refuses wrong authenticated nonce despite GH private' pf_signin_with_gh_readback private invalid

  forwarding_origin_pinned() (
    export CODESPACE_NAME=fixture
    unset GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN
    [[ $(c1_codespace_base 6080) == https://fixture-6080.app.github.dev ]]
    GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN=app.github.dev c1_codespace_base 6080 >/dev/null
    local host
    for host in attacker.example app.github.dev.evil.example app-github.dev app.github.dev:443 App.Github.Dev; do
      if GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN=$host c1_codespace_base 6080; then return 1; fi
      if GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN=$host c1_codespace_url 6080; then return 1; fi
    done
  )
  untrusted_origin_never_receives_header() (
    local trace="$TMP/untrusted-curl-trace"
    : >"$trace"
    export CODESPACE_NAME=fixture
    unset GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN
    c1_https_probe() {
      local out=$2 hdr=${3:-}
      printf 'probe\n' >>"$trace"
      if [[ -n "$hdr" ]]; then printf 'marker\n' >"$out"; printf '200 \n'; else printf '403 \n'; fi
    }
    if c1_privacy_check 'https://fixture-6080.attacker.example' /marker marker "$PV/hdr" "$CA"; then return 1; fi
    [[ ! -s "$trace" && $C1_PRIVACY_STATE == unverified ]]
  )
  forwarding_origin_mutation() (
    local original mutated
    original=$(declare -f c1_privacy_check)
    mutated=$(awk 'index($0, "if [[ \"$base\" != \"$(c1_codespace_base") && !seen { drop=1; seen=1 }
      drop && /^[[:space:]]*fi;/ { drop=0; next } !drop { print }' <<<"$original")
    [[ $mutated != "$original" ]] || return 1
    eval "$mutated"
    ! untrusted_origin_never_receives_header
  )
  t 'L-B: only the exact trusted forwarding domain can construct a Codespaces URL' forwarding_origin_pinned
  t 'L-B: an untrusted forwarding origin is rejected before any token-bearing probe' untrusted_origin_never_receives_header
  t 'L-B: token destination test detects removal of the origin guard' forwarding_origin_mutation

  curl_probe_no_user_config() (
    local bin="$TMP/curl-probe-bin" out="$TMP/curl-probe-body" result
    mkdir -p "$bin"
    cat >"$bin/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
[[ "$1" == --disable ]] || exit 9
if [[ "$*" == *partial* ]]; then printf '200 '; exit 18; fi
printf '403 '
MOCK_CURL
    chmod +x "$bin/curl"
    export PATH="$bin:$PATH"
    result=$(c1_https_probe "$BASE/marker" "$out") && [[ $result == '403 ' ]] || return 1
    result=$(c1_https_probe "$BASE/partial" "$out" "$PV/hdr") && [[ $result == '000 ' ]]
  )
  curl_probe_config_mutation() (
    local original mutated
    original=$(declare -f c1_https_probe)
    mutated=${original/'curl --disable '/'curl '}
    [[ $mutated != "$original" ]] || return 1
    eval "$mutated"
    ! curl_probe_no_user_config
  )
  t 'L-B/R-3: curl disables user config and rejects partial success on a failed transfer' curl_probe_no_user_config
  t 'L-B: probe test detects removal of curl config isolation' curl_probe_config_mutation

  gh_readback() (
    local mode=$1 bin="$TMP/gh-readback-$1" start elapsed rc=0
    # shellcheck disable=SC2034  # dynamically read by the sourced library
    local C1_GH_READ_TIMEOUT=0.2
    mkdir -p "$bin"
    cat >"$bin/gh" <<'MOCK_GH'
#!/usr/bin/env bash
case $GH_FIXTURE in
  private) printf '[{"sourcePort":6080,"visibility":"private"}]\n' ;;
  duplicate) printf '[{"sourcePort":6080,"visibility":"private"},{"sourcePort":6080,"visibility":"public"}]\n' ;;
  wrong-port) printf '[{"sourcePort":5173,"visibility":"private"}]\n' ;;
  malformed) printf '{"sourcePort":6080,"visibility":"private"}\n' ;;
  invalid-status) printf '[{"sourcePort":6080,"visibility":"private\\ninvalid"}]\n' ;;
  command-failed) printf '[{"sourcePort":6080,"visibility":"private"}]\n'; exit 1 ;;
  hang)
    trap '' TERM
    printf '[{"sourcePort":6080,"visibility":"private"}]\n'
    exec sleep 3
    ;;
esac
MOCK_GH
    chmod +x "$bin/gh"
    export PATH="$bin:$PATH" CODESPACE_NAME=c1-test GH_FIXTURE=$mode
    start=${EPOCHREALTIME/./}
    c1_gh_private 6080 || rc=$?
    elapsed=$(( ${EPOCHREALTIME/./} - start ))
    if [[ $mode == private ]]; then
      (( rc == 0 )) && [[ $C1_GH_VIS == private ]]
    else
      (( rc == 2 && elapsed < 2000000 )) && [[ -z $C1_GH_VIS ]]
    fi
  )
  gh_timeout_mutation() (
    local original mutated remove='timeout --kill-after=0.1s "$C1_GH_READ_TIMEOUT" '
    original=$(declare -f c1_gh_visibility)
    mutated=${original/"$remove"/}
    [[ $mutated != "$original" ]] || return 1
    eval "$mutated"
    ! gh_readback hang
  )
  t 'gh read-back accepts one exact private port' gh_readback private
  t 'gh read-back rejects duplicate records for the protected port' gh_readback duplicate
  t 'gh read-back rejects private visibility for a different port' gh_readback wrong-port
  t 'gh read-back rejects malformed JSON shape' gh_readback malformed
  t 'gh read-back rejects malformed visibility text' gh_readback invalid-status
  t 'gh read-back rejects valid JSON from a failed CLI' gh_readback command-failed
  t 'gh read-back times out a TERM-ignoring CLI and discards partial private output' gh_readback hang
  t 'gh read-back test detects removal of the actual timeout' gh_timeout_mutation

  pf_signin_https_integration() (
    local s ws wsst bin="$TMP/pf-integration-bin" start elapsed
    # shellcheck disable=SC2034  # dynamically read by the sourced library
    local C1_NOVNC_PORT=$DP C1_PRIVACY_TIMEOUT=4
    s=$(c1_session_new "$ROOT")
    trap 'mode private; c1_cleanup_session "$ROOT" "$s"' EXIT
    export C1_HARNESS_SESSION=$s CODESPACE_NAME=fixture-only
    export GH_FIXTURE_STATE="$s/visibility" GH_FIXTURE_CALLS="$s/gh-calls" GH_FIXTURE_PORT=$DP
    mkdir -p "$bin" "$s/web"
    cat >"$bin/gh" <<'MOCK_GH'
#!/usr/bin/env bash
printf '%s\n' called >>"$GH_FIXTURE_CALLS"
case $(cat "$GH_FIXTURE_STATE") in
  private) printf '[{"sourcePort":%s,"visibility":"private"}]\n' "$GH_FIXTURE_PORT" ;;
  hang)
    trap '' TERM
    printf '[{"sourcePort":%s,"visibility":"private"}]\n' "$GH_FIXTURE_PORT"
    sleep 2
    ;;
esac
MOCK_GH
    chmod +x "$bin/gh"
    export PATH="$bin:$PATH"
    # Only the expected origin is redirected to the loopback TLS fixture.
    c1_codespace_base() { [[ $1 == "$DP" ]] && printf '%s' "$BASE"; }
    mode pf-signin
    printf 'private\n' >"$GH_FIXTURE_STATE"
    if c1_privacy_check "$BASE" /marker marker "$PV/bad.hdr" "$CA"; then return 1; fi
    [[ $C1_PRIVACY_STATE == unverified ]]
    : >"$GH_FIXTURE_CALLS"
    c1_verify_private_with_decoy "$s" "$DP" "$BASE" "$PV/hdr" "$CA"
    [[ $(wc -l <"$GH_FIXTURE_CALLS") == 1 ]]

    printf '<title>noVNC</title>\n' >"$s/web/vnc.html"
    ws=$(c1_spawn "$s" desktop-fixture python3 -I -m http.server --bind 127.0.0.1 --directory "$s/web" "$DP")
    wsst=$(c1_proc_start "$ws")
    for _ in $(seq 1 20); do c1_port_free "$DP" || break; sleep 0.05; done
    c1_privacy_gate "$s" "$DP" "$BASE" "$PV/hdr" "$ws" "$wsst" 1 "$CA"
    [[ $(wc -l <"$GH_FIXTURE_CALLS") == 2 ]]

    # R-1: the monitor's pf-signin sweeps use its budgeted gh evidence (no gh
    # call per sweep). A hanging gh (ignores TERM) never pauses the sweeps; once
    # the last private read is older than the freshness bound, noVNC is torn down.
    # Test-only scaled schedule; production values are asserted separately.
    local C1_MON_GH_INTERVAL_MS=500 C1_MON_GH_RETRY_MS=500 C1_MON_GH_TIMEOUT_MS=500 C1_MON_GH_MAX_AGE_MS=2000
    printf 'hang\n' >"$GH_FIXTURE_STATE"
    : >"$GH_FIXTURE_CALLS"
    start=${EPOCHREALTIME/./}
    c1_privacy_monitor_loop "$s" "$BASE" "$PV/hdr" "$ws" "$wsst" "$CA" 0.2 "$DP" 1
    elapsed=$(( ${EPOCHREALTIME/./} - start ))
    (( elapsed >= 2000000 && elapsed < 4000000 )) || { echo "elapsed ${elapsed}us"; return 1; }
    local calls; calls=$(wc -l <"$GH_FIXTURE_CALLS")
    (( calls >= 1 && calls <= 3 )) || { echo "gh calls $calls"; return 1; }   # 2 s / 0.5 s retry, one in flight
    grep -q ' gh-stale$' "$s/privacy-failed"
    c1_port_free "$DP"
  )
  t 'HTTPS pf-signin redirect uses the decoy, desktop gate and budgeted gh-stale monitor teardown (R-1)' pf_signin_https_integration

  pf_signin_https_reject() (
    local route=$1 trace="$PV/state/authenticated-probes"
    : >"$trace"
    trap 'mode private' EXIT
    mode "$route"
    c1_codespace_base() {
      if [[ $route == pf-signin ]]; then printf 'https://other-6080.app.github.dev'; else printf '%s' "$BASE"; fi
    }
    if c1_privacy_check "$BASE" /marker marker "$PV/hdr" "$CA"; then return 1; fi
    [[ $C1_PRIVACY_STATE == unverified && ! -s "$trace" ]]
  )
  for route in pf-wrong-scheme pf-wrong-host pf-wrong-path pf-wrong-status pf-userinfo pf-fragment pf-signin; do
    t "R-3: TLS forwarder refuses $route without token delivery (pf-signin case uses a mismatched origin)" pf_signin_https_reject "$route"
  done
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
      # Test-only scaled gh schedule (production values are asserted separately).
      c1_spawn "$s" privacy-monitor bash -c 'source "$1"; C1_MON_GH_INTERVAL_MS=1000 C1_MON_GH_RETRY_MS=500 C1_MON_GH_TIMEOUT_MS=1000 C1_MON_GH_MAX_AGE_MS=3000; c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "$7" 1 "$8" "$9"' \
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
    monitor_gh_unavailable() {   # L-10/R-1: lost gh read-back -> torn down once the last private read is stale
      local s ws wsst; usegh; echo private >"$PV/state/ghvis"
      s=$(c1_session_new "$ROOT"); export C1_HARNESS_SESSION=$s
      read -r ws wsst <<<"$(start_mon "$s" private 1)"
      sleep 1.5; alive "$ws"
      echo fail >"$PV/state/ghvis"
      local ok=0; closed_within "$DP" 8 && ok=1
      grep -q ' gh-stale$' "$s/privacy-failed" || ok=0
      echo private >"$PV/state/ghvis"; unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
      (( ok == 1 ))
    }
    t 'privacy monitor tears noVNC down when the gh read-back becomes unavailable (L-10, gh-stale)' monitor_gh_unavailable
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

# ---- R-1: budgeted, freshness-bounded gh read-back in the privacy monitor ----
# The real monitor loop runs with a stub gh (call log, latency, concurrency
# probe) and stubbed HTTPS probes/teardown. Schedules are scaled for speed;
# the production schedule is asserted by the static case above and below.
r1_run() (   # r1_run SECONDS LATENCY MODE [SWITCH_AFTER SWITCH_MODE] -> "rc elapsed_ms calls probes maxconc reason"
  local secs=$1 lat=$2 mode=$3 sw_after=${4:-} sw_mode=${5:-} d="$TMP/r1.$BASHPID" t0 rc=0 el
  rm -rf "$d"; mkdir -p "$d/bin" "$d/s/tmp"
  printf 'X-Github-Token: fixture\n' >"$d/hdr"; printf '%s\n' "$mode" >"$d/mode"
  : >"$d/gh.log"; : >"$d/conc"; : >"$d/probes"
  cat >"$d/bin/gh" <<GH
#!/usr/bin/env bash
d=$(printf '%q' "$d")
echo call >>"\$d/gh.log"
mkdir "\$d/inflight.\$\$"; trap 'rmdir "\$d/inflight.\$\$" 2>/dev/null' EXIT
ls -d "\$d"/inflight.* | wc -l >>"\$d/conc"
sleep $(printf '%q' "$lat") & wait \$!
m=\$(cat "\$d/mode")
case \$m in private|org|public) printf '[{"sourcePort":6080,"visibility":"%s"}]\n' "\$m" ;; *) exit 1 ;; esac
GH
  chmod 755 "$d/bin/gh"
  export PATH="$d/bin:$PATH" CODESPACE_NAME=r1-fixture TMPDIR="$d/s/tmp"
  c1_owned_pid() { [[ ! -e "$d/stop" ]]; }
  c1_privacy_check() { echo probe >>"$d/probes"; C1_PRIVACY_STATE=private; return 0; }
  c1_teardown_novnc() { printf '%s\n' "$5" >"$d/reason"; return 0; }
  ( sleep "$secs"; : >"$d/stop" ) & local stopper=$!
  if [[ -n "$sw_after" ]]; then ( sleep "$sw_after"; printf '%s\n' "$sw_mode" >"$d/mode" ) & fi
  t0=${EPOCHREALTIME//[!0-9]/}
  c1_privacy_monitor_loop "$d/s" https://example.invalid "$d/hdr" 1 1 '' 0.1 6080 1 || rc=$?
  el=$(( (${EPOCHREALTIME//[!0-9]/} - t0) / 1000 ))
  kill "$stopper" 2>/dev/null || true; wait 2>/dev/null || true
  printf '%s %s %s %s %s %s\n' "$rc" "$el" "$(wc -l <"$d/gh.log")" "$(wc -l <"$d/probes")" \
    "$(sort -n "$d/conc" | tail -1)" "$(cat "$d/reason" 2>/dev/null || echo none)"
)
r1_slow() (    # realistic 1.5 s latency under the PRODUCTION timeout: never torn down, sweeps never pause
  local C1_MON_GH_INTERVAL_MS=1000 C1_MON_GH_RETRY_MS=500 C1_MON_GH_MAX_AGE_MS=4000   # timeout: production 15 s
  local rc el calls probes conc reason
  read -r rc el calls probes conc reason <<<"$(r1_run 6 1.5 private)"
  echo "rc=$rc el=$el calls=$calls probes=$probes conc=$conc reason=$reason"
  (( rc == 0 )) && [[ $reason == none ]] && (( calls >= 2 && calls <= 4 && probes >= 35 && conc == 1 ))
)
t 'R-1: slow gh (1.5 s) under the 15 s production timeout: no teardown, HTTPS sweeps continue, one read in flight' r1_slow
r1_timeout() ( # gh never answers in time: no teardown until the evidence is stale, then gh-stale
  local C1_MON_GH_INTERVAL_MS=500 C1_MON_GH_RETRY_MS=500 C1_MON_GH_TIMEOUT_MS=300 C1_MON_GH_MAX_AGE_MS=2000
  local rc el calls probes conc reason
  read -r rc el calls probes conc reason <<<"$(r1_run 8 5 private)"
  echo "rc=$rc el=$el calls=$calls conc=$conc reason=$reason"
  (( rc == 0 )) && [[ $reason == gh-stale ]] && (( el >= 2000 && el < 3500 && calls >= 1 && calls <= 5 && conc == 1 ))
)
t 'R-1: gh timeouts tear noVNC down as gh-stale once the private evidence exceeds its age bound' r1_timeout
r1_changed() ( # visibility changes to org/public: torn down on the next budgeted read
  local vis=$1 C1_MON_GH_INTERVAL_MS=500 C1_MON_GH_RETRY_MS=500 C1_MON_GH_TIMEOUT_MS=1000 C1_MON_GH_MAX_AGE_MS=5000
  local rc el calls probes conc reason
  read -r rc el calls probes conc reason <<<"$(r1_run 8 0.2 private 1.5 "$vis")"
  echo "rc=$rc el=$el calls=$calls reason=$reason"
  (( rc == 0 )) && [[ $reason == "gh-visibility-$vis" ]] && (( el >= 1500 && el < 3000 ))
)
t 'R-1: gh visibility changed to organization: teardown on the next budgeted read' r1_changed org
t 'R-1: gh visibility changed to public: teardown on the next budgeted read' r1_changed public
r1_stale() (   # gh fails outright: retried on budget, torn down exactly at staleness, not before
  local C1_MON_GH_INTERVAL_MS=500 C1_MON_GH_RETRY_MS=500 C1_MON_GH_TIMEOUT_MS=1000 C1_MON_GH_MAX_AGE_MS=2000
  local rc el calls probes conc reason
  read -r rc el calls probes conc reason <<<"$(r1_run 8 0 fail)"
  echo "rc=$rc el=$el calls=$calls reason=$reason"
  (( rc == 0 )) && [[ $reason == gh-stale ]] && (( el >= 2000 && el < 2700 && calls >= 2 && calls <= 4 ))
)
t 'R-1: stale evidence: failing gh is retried on budget and noVNC is torn down at the age bound' r1_stale
r1_budget() (  # r1_budget INTERVAL RETRY MODE CLAIMED_GAP_MS -> read count within the claimed bound over 5 s
  local C1_MON_GH_INTERVAL_MS=$1 C1_MON_GH_RETRY_MS=$2 C1_MON_GH_TIMEOUT_MS=1000 C1_MON_GH_MAX_AGE_MS=60000
  local rc el calls probes conc reason gap=$4
  read -r rc el calls probes conc reason <<<"$(r1_run 5 0 "$3")"
  echo "rc=$rc el=$el calls=$calls probes=$probes conc=$conc reason=$reason bound=$(( el / gap + 1 ))"
  (( rc == 0 )) && [[ $reason == none ]] && (( calls >= 1 && calls <= el / gap + 1 && conc <= 1 ))
)
t 'R-1: bounded request count: private reads at most one per interval (5 s run)' r1_budget 500 250 private 500
t 'R-1: bounded request count: failing reads at most one per retry gap (5 s run)' r1_budget 500 250 fail 250
f 'R-1 control: an unbudgeted schedule (no interval) violates the request bound' r1_budget 0 0 private 500
r1_production_budget() (   # the documented production budget is self-consistent
  (( 3600000 / C1_MON_GH_INTERVAL_MS == 120 && 3600000 / C1_MON_GH_RETRY_MS == 360 ))
  (( C1_MON_GH_MAX_AGE_MS >= C1_MON_GH_INTERVAL_MS + 2 * C1_MON_GH_TIMEOUT_MS ))   # one slow success never goes stale
  (( C1_MON_GH_TIMEOUT_MS >= 10000 && C1_GH_READ_TIMEOUT >= 10 ))                  # assumed gh latency: a few seconds
  grep -Fq 'Request budget (R-1)' "$HERE/../README.md" && grep -Fq '360' "$HERE/../README.md"
)
t 'R-1: production gh budget (120/h nominal, 360/h cap, 75 s freshness, 15 s timeout) is documented and consistent' r1_production_budget

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

t 'L-6/R-1: monitor HTTPS probes stay 0.4s; gh uses the documented budget and realistic timeout' bash -c \
  'grep -Fq "local C1_PROBE_MAX_TIME=0.4 C1_MONITOR_FAST_TEARDOWN=1" "$1" &&
   grep -Fq "local C1_GH_READ_TIMEOUT C1_GH_EVIDENCE=cached" "$1" &&
   grep -Fxq "C1_GH_READ_TIMEOUT=15" "$1" && grep -Fxq "C1_MON_GH_TIMEOUT_MS=15000" "$1" &&
   grep -Fxq "C1_MON_GH_INTERVAL_MS=30000" "$1" && grep -Fxq "C1_MON_GH_RETRY_MS=10000" "$1" &&
   grep -Fxq "C1_MON_GH_MAX_AGE_MS=75000" "$1" &&
   grep -Fq "local wait_steps=20" "$1" &&
   grep -Fq "wait_steps=5" "$1" &&
   grep -Fq -- "--max-time" "$1" && grep -Fq "C1_PROBE_MAX_TIME" "$1"' _ "$HERE/../lib/c1-lib.sh"

echo '# IPv4/IPv6 listener evidence: adversarial regressions (H-1, M-1..M-3, L-1..L-3)'
# Every case drives the REAL parsers and enforcement functions. Real marked
# processes hold real sockets with real inodes; only the kernel tables
# (C1_PROC_NET) and ss (C1_SS_BIN) are replaced by fixtures or fakes where a
# condition cannot be produced on demand (e.g. IPv6 on an IPv6-less runner).
LIB="$HERE/../lib/c1-lib.sh"
ADV="$TMP/adv"; mkdir -p "$ADV/bin"
REAL_SS=$(command -v ss || true)
PY3=$(command -v python3 || true)
HAVE_V6=0
python3 -I -c 'import socket; s=socket.socket(socket.AF_INET6); s.bind(("::1", 0)); s.close()' 2>/dev/null && HAVE_V6=1
# Fake ss: modes fail | empty | file | rewrite (real ss with sed edits from FAKE_SS_SED).
cat >"$ADV/bin/ss" <<'SS'
#!/usr/bin/env bash
case ${FAKE_SS_MODE:-real} in
  fail)    echo 'Cannot open netlink socket: Permission denied' >&2; exit 1 ;;
  empty)   exit 0 ;;
  garbage) printf 'State Recv-Q Send-Q\nnot a socket row at all\n'; exit 0 ;;
  file)    cat "$FAKE_SS_FILE" ;;
  rewrite) "$REAL_SS" "$@" | sed -E "${FAKE_SS_SED:-}" ;;
  *)       exec "$REAL_SS" "$@" ;;
esac
SS
chmod 755 "$ADV/bin/ss"
export REAL_SS
TCP6_HDR='  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode'
TCP4_HDR='  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode'
# v6row SL HEXADDR32 PORTHEX INODE [STATE]
v6row() { printf '%4s: %s:%s 00000000000000000000000000000000:0000 %s 00000000:00000000 00:00000000 00000000     0        0 %s 1 0000000000000000 100 0 0 10 0\n' "$1" "$2" "$3" "${5:-0A}" "$4"; }
v4row() { printf '%4s: %s:%s 00000000:0000 %s 00000000:00000000 00:00000000 00000000     0        0 %s 1 0000000000000000 100 0 0 10 0\n' "$1" "$2" "$3" "${5:-0A}" "$4"; }
hex4() { printf '%04X' "$1"; }

# listen_on SESSION NAME HOST [spawn|marked|foreign] [PORT] -> "pid port inode"
listen_on() {
  local s=$1 name=$2 host=$3 how=${4:-spawn} want=${5:-0} pid log i port ino
  log="$s/log/$name.log"
  local code='import socket,sys,os,time
h=sys.argv[1]; fam=socket.AF_INET6 if ":" in h else socket.AF_INET
s=socket.socket(fam, socket.SOCK_STREAM)
if fam==socket.AF_INET6: s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
s.bind((h, int(sys.argv[2]))); s.listen(1)
print(s.getsockname()[1], os.fstat(s.fileno()).st_ino, flush=True)
time.sleep(120)'
  case $how in
    spawn)   pid=$(c1_spawn "$s" "$name" python3 -I -c "$code" "$host" "$want") ;;
    marked)  env C1_HARNESS_SESSION="$s" python3 -I -c "$code" "$host" "$want" >"$log" 2>&1 </dev/null & pid=$! ;;
    foreign) env -u C1_HARNESS_SESSION setsid "$PY3" -I -c "$code" "$host" "$want" >"$log" 2>&1 </dev/null & pid=$! ;;
  esac
  for (( i = 0; i < 100; i++ )); do [[ -s "$log" ]] && break; sleep 0.05; done
  read -r port ino <"$log"
  [[ "$port" =~ ^[0-9]+$ && "$ino" =~ ^[0-9]+$ ]] || { echo "listener $name did not start" >&2; return 1; }
  printf '%s %s %s\n' "$pid" "$port" "$ino"
}
# fixture_net DIR: snapshot real kernel tables into writable private test
# fixtures. /proc/net entries are read-only; copying their modes would make
# synthetic IPv4/IPv6 row injection fail (or prompt during replacement).
fixture_net() {
  local d=$1
  mkdir -p "$d"
  cp -- /proc/net/tcp "$d/tcp"
  chmod u+w -- "$d/tcp"
  if [[ -e /proc/net/tcp6 ]]; then
    cp -- /proc/net/tcp6 "$d/tcp6"
    chmod u+w -- "$d/tcp6"
  fi
}
fixture_net_writable() {
  local d="$ADV/fixture-permissions"
  fixture_net "$d"
  [[ -w "$d/tcp" ]] && test -s "$d/tcp"
  printf '# fixture\n' >>"$d/tcp"
  if [[ -e /proc/net/tcp6 ]]; then
    [[ -w "$d/tcp6" ]] && test -s "$d/tcp6"
    printf '# fixture\n' >>"$d/tcp6"
  fi
}
t 'private kernel-table fixtures are writable without modifying /proc/net' fixture_net_writable
# move_to_v6 DIR INODE PORT HEXADDR32: the real socket INODE now appears ONLY as an IPv6 listener.
move_to_v6() {
  local d=$1 ino=$2 port=$3 addr=$4
  awk -v ino="$ino" 'NR == 1 || $10 != ino' "$d/tcp" >"$d/tcp.new" && /usr/bin/mv -f -- "$d/tcp.new" "$d/tcp"
  [[ -e "$d/tcp6" ]] || printf '%s\n' "$TCP6_HDR" >"$d/tcp6"
  v6row 90 "$addr" "$(hex4 "$port")" "$ino" >>"$d/tcp6"
}
newsess() { local s; s=$(c1_session_new "$ROOT"); printf '%s' "$s"; }
endsess() { unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$1"; }

# ---- parsers: kernel tables -------------------------------------------------
parse_v6_rows() {
  local f="$ADV/p6"; { printf '%s\n' "$TCP6_HDR"
    v6row 0 00000000000000000000000000000000 170C 101           # [::]:5900
    v6row 1 00000000000000000000000001000000 1F90 102           # [::1]:8080
    v6row 2 0000000000000000FFFF00000100007F 0050 103           # [::ffff:127.0.0.1]:80
    v6row 3 B80D0120000000000000000001000000 0016 104           # [2001:db8::1]:22
    v6row 4 00000000000000000000000001000000 1F91 105 01        # ESTABLISHED: ignored
  } >"$f"
  local got; got=$(c1_parse_proc_net_listen 6 "$f")
  [[ "$got" == $'101 [::]:5900\n102 [::1]:8080\n103 [::ffff:7f00:1]:80\n104 [2001:db8::1]:22' ]]
}
t 'tcp6 parser: wildcard [::], ::1, IPv4-mapped and global IPv6 LISTEN rows decoded; non-LISTEN ignored' parse_v6_rows
parse_v4_rows() {
  local f="$ADV/p4"; { printf '%s\n' "$TCP4_HDR"; v4row 0 00000000 170C 201; v4row 1 0100007F 17B0 202; v4row 2 0400000A 1F90 203 06; } >"$f"
  [[ "$(c1_parse_proc_net_listen 4 "$f")" == $'201 0.0.0.0:5900\n202 127.0.0.1:6064' ]]
}
t 'tcp parser: 0.0.0.0 wildcard and loopback IPv4 LISTEN rows decoded; TIME_WAIT ignored' parse_v4_rows
bad_table() {   # bad_table FAMILY CONTENT  -> parser must FAIL
  printf '%s' "$2" >"$ADV/bad"; c1_parse_proc_net_listen "$1" "$ADV/bad" >/dev/null
}
f 'tcp6 parser: empty table is a failure, not "no listeners"' bad_table 6 ''
f 'tcp6 parser: missing header is a failure' bad_table 6 "$(v6row 0 00000000000000000000000000000000 170C 1)"$'\n'
f 'tcp6 parser: truncated address is a failure' bad_table 6 "$TCP6_HDR"$'\n'"   0: 0000000000000000:170C 00000000000000000000000000000000:0000 0A 0 0 0 0 0 77"$'\n'
f 'tcp6 parser: non-numeric inode is a failure' bad_table 6 "$TCP6_HDR"$'\n'"$(v6row 0 00000000000000000000000000000000 170C x9)"$'\n'
f 'tcp6 parser: LISTEN row without an inode (0) is a failure' bad_table 6 "$TCP6_HDR"$'\n'"$(v6row 0 00000000000000000000000000000000 170C 0)"$'\n'
f 'tcp parser: IPv4 row in an IPv6 table (wrong family width) is a failure' bad_table 6 "$TCP6_HDR"$'\n'"$(v4row 0 00000000 170C 5)"$'\n'
f 'tcp parser: unreadable table is a failure' c1_parse_proc_net_listen 4 "$ADV/does-not-exist"
v6_table_missing_but_v6_present() {
  local d="$ADV/nov6"; mkdir -p "$d"; cp /proc/net/tcp "$d/tcp"; : >"$d/if_inet6"
  C1_PROC_NET=$d; c1_proc_listen_table >/dev/null
}
f 'IPv6 configured (if_inet6) but no tcp6 table: evidence unavailable, fail closed' v6_table_missing_but_v6_present
if (( HAVE_V6 )); then
  v6_table_hidden_on_v6_host() { local d="$ADV/hid6"; mkdir -p "$d"; cp /proc/net/tcp "$d/tcp"; C1_PROC_NET=$d; c1_proc_listen_table >/dev/null; }
  f 'IPv6-capable kernel with the tcp6 table hidden: fail closed' v6_table_hidden_on_v6_host
else skip 'IPv6-capable kernel with hidden tcp6 table (runner has no IPv6)'; fi

# ---- parsers: ss --------------------------------------------------------------
parse_ss_ok() {
  local got; got=$(c1_parse_ss_listen <<'EOF'
LISTEN 0      5      127.0.0.1:6080   0.0.0.0:* ino:11 sk:1 cgroup:/ <->
LISTEN 0      5           [::]:5900      [::]:* uid:1000 ino:12 sk:2 v6only:1 <->
LISTEN 0      5              *:5901         *:* ino:13 sk:3 <->
LISTEN 0      4096 127.0.0.53%lo:53   0.0.0.0:* uid:101 ino:14 sk:4 <->
LISTEN 0      5          [::1]:631     [::]:* ino:15 sk:5 <->
EOF
)
  [[ "$got" == $'11 127.0.0.1:6080\n12 [::]:5900\n13 [::]:5901\n14 127.0.0.53:53\n15 [::1]:631' ]]
}
t 'ss parser: IPv4, [::], dual-stack "*", scoped and ::1 rows canonicalised with inodes' parse_ss_ok
f 'ss parser: row without ino: is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"LISTEN 0 5 127.0.0.1:6080 0.0.0.0:* sk:1"' _ "$LIB"
f 'ss parser: row with two inodes is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"LISTEN 0 5 [::]:5900 [::]:* ino:1 ino:2"' _ "$LIB"
f 'ss parser: non-LISTEN state is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"ESTAB 0 0 127.0.0.1:6080 127.0.0.1:5555 ino:9"' _ "$LIB"
f 'ss parser: header/garbage row is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"State Recv-Q Send-Q Local Peer"' _ "$LIB"
f 'ss parser: malformed local address is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"LISTEN 0 5 [:::]:5900 *:* ino:3"' _ "$LIB"
f 'ss parser: truncated row is a failure' bash -c 'source "$1"; c1_parse_ss_listen <<<"LISTEN 0 5 127.0.0.1:6080"' _ "$LIB"
f 'ss failing (non-zero exit) is a failure, not an empty listener set' bash -c 'source "$1"; C1_SS_BIN=$2; FAKE_SS_MODE=fail c1_ss_listen_table' _ "$LIB" "$ADV/bin/ss"
f 'ss printing garbage is a failure' bash -c 'source "$1"; C1_SS_BIN=$2; FAKE_SS_MODE=garbage c1_ss_listen_table' _ "$LIB" "$ADV/bin/ss"
f 'ss binary missing is a failure' bash -c 'source "$1"; C1_SS_BIN=/nonexistent/ss; c1_ss_listen_table' _ "$LIB"

if [[ -n "$REAL_SS" ]] && command -v python3 >/dev/null; then
  # ---- blindness and agreement --------------------------------------------
  ss_blind_empty() {   # kernel shows listeners, ss shows none
    local s; s=$(newsess); export C1_HARNESS_SESSION=$s
    listen_on "$s" l1 127.0.0.1 spawn >/dev/null
    C1_SS_BIN="$ADV/bin/ss"; export FAKE_SS_MODE=empty
    local rc=0; c1_listen_tables || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'ss empty while kernel tables list listeners: evidence rejected (empty output is not success)' ss_blind_empty
  real_tables_agree() { c1_listen_tables && [[ -n "$C1_LT_PROC" ]] && [[ "$(LC_ALL=C sort <<<"$C1_LT_PROC")" == "$(LC_ALL=C sort <<<"$C1_LT_SS")" ]]; }
  t 'real kernel tables and real ss agree inode-for-inode on this runner' real_tables_agree

  # ---- attribution: IPv4, IPv6, dual-stack, wildcard ------------------------
  v4_loopback_attributed() {
    local s pid port ino got; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" pyl 127.0.0.1 spawn)
    got=$(c1_harness_listeners "$s")
    endsess "$s"
    [[ "$got" == "127.0.0.1:$port pid=$pid comm=python3 ino=$ino" ]]
  }
  t 'IPv4 loopback listener attributed by socket inode to the exact pid/comm' v4_loopback_attributed
  v4_wildcard_pre_novnc() {
    local s pid port ino rc=0 err; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" wild 0.0.0.0 spawn)
    err=$(c1_assert_no_listeners_before_novnc "$s" 2>&1) || rc=$?
    local st; st=$(sed -n 1p "$s/listener-state")
    endsess "$s"
    (( rc != 0 )) && [[ "$st" == failed-unexpected ]] \
      && [[ "$err" == *"0.0.0.0:$port pid=$pid comm=python3 ino=$ino"* ]]   # M-2 diagnostic: addr, pid, comm, inode
  }
  t 'wildcard IPv4 (0.0.0.0) listener before noVNC aborts with addr/pid/comm/inode diagnostic (M-2)' v4_wildcard_pre_novnc
  v6only_wildcard_pre_novnc() {   # x11vnc H-1 shape: [::]:5900 with IPV6_V6ONLY, IPv6-only
    local s pid port ino d="$ADV/v6only" rc=0 err; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" x11vnc-like 127.0.0.1 spawn)
    fixture_net "$d"; move_to_v6 "$d" "$ino" 5900 00000000000000000000000000000000
    C1_PROC_NET=$d C1_SS_BIN="$ADV/bin/ss"
    export FAKE_SS_MODE=rewrite FAKE_SS_SED="s/^(LISTEN +[0-9]+ +[0-9]+ +)127\.0\.0\.1:$port( .* ino:$ino )/\1[::]:5900\2/"
    err=$(c1_assert_no_listeners_before_novnc "$s" 2>&1) || rc=$?
    endsess "$s"
    (( rc != 0 )) && [[ "$err" == *"[::]:5900 pid=$pid comm=python3 ino=$ino"* ]]
  }
  t 'IPv6-only wildcard [::]:5900 listener (H-1 shape) is detected and aborts startup' v6only_wildcard_pre_novnc
  dual_stack_both_reported() {
    local s p1 o1 p2 o2 i2 d="$ADV/dual" got; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r p1 o1 _ < <(listen_on "$s" v4 0.0.0.0 spawn)
    read -r p2 o2 i2 < <(listen_on "$s" v6 127.0.0.1 spawn)
    fixture_net "$d"; move_to_v6 "$d" "$i2" "$o1" 00000000000000000000000000000000
    C1_PROC_NET=$d C1_SS_BIN="$ADV/bin/ss"
    export FAKE_SS_MODE=rewrite FAKE_SS_SED="s/^(LISTEN +[0-9]+ +[0-9]+ +)127\.0\.0\.1:$o2( .* ino:$i2 )/\1[::]:$o1\2/"
    got=$(c1_harness_listeners "$s")
    endsess "$s"
    [[ "$got" == *"0.0.0.0:$o1 pid=$p1 "* && "$got" == *"[::]:$o1 pid=$p2 "* ]] && (( $(wc -l <<<"$got") == 2 ))
  }
  t 'dual-stack (0.0.0.0 + [::] on one port) reports BOTH sockets' dual_stack_both_reported
  v6_loopback_extra_rejected() {   # loopback, but not the single expected noVNC socket
    local s pw ow iw o6 i6 d="$ADV/v6lo" rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pw ow iw < <(listen_on "$s" websockify 127.0.0.1 spawn)
    read -r _ o6 i6 < <(listen_on "$s" extra 127.0.0.1 spawn)
    fixture_net "$d"; move_to_v6 "$d" "$i6" "$ow" 00000000000000000000000001000000
    C1_PROC_NET=$d C1_SS_BIN="$ADV/bin/ss"
    export FAKE_SS_MODE=rewrite FAKE_SS_SED="s/^(LISTEN +[0-9]+ +[0-9]+ +)127\.0\.0\.1:$o6( .* ino:$i6 )/\1[::1]:$ow\2/"
    c1_assert_desktop_listeners "$s" "$ow" 2>/dev/null || rc=$?
    local st; st=$(sed -n 1p "$s/listener-state"); endsess "$s"
    (( rc != 0 )) && [[ "$st" == failed-unexpected ]]
  }
  t 'expected 127.0.0.1 noVNC plus an extra [::1] listener on the same port: rejected' v6_loopback_extra_rejected
  expected_only_verified() {
    local s pw ow iw st det; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pw ow iw < <(listen_on "$s" websockify 127.0.0.1 spawn)
    c1_assert_desktop_listeners "$s" "$ow"
    { read -r st; read -r det; } <"$s/listener-state"; endsess "$s"
    [[ "$st" == verified && "$det" == "127.0.0.1:$ow pid=$pw comm=python3 ino=$iw" ]]
  }
  t 'exactly the expected loopback socket: verified, with sanitized inode evidence recorded' expected_only_verified
  unexpected_loopback_port() {
    local s pw ow iw rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pw ow iw < <(listen_on "$s" other 127.0.0.1 spawn)
    c1_assert_desktop_listeners "$s" $(( ow == 65535 ? 1 : ow + 1 )) 2>/dev/null || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'a loopback listener on an unexpected port is rejected' unexpected_loopback_port
  no_listener_not_verified() {
    local s rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    c1_assert_desktop_listeners "$s" 6080 2>/dev/null || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'no listener at all after noVNC start is never "verified"' no_listener_not_verified
  if (( HAVE_V6 )); then
    real_v6_wildcard() {   # REAL IPv6 socket, REAL kernel table, REAL ss
      local s pid port ino got rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
      read -r pid port ino < <(listen_on "$s" realv6 :: spawn)
      got=$(c1_harness_listeners "$s")
      c1_assert_no_listeners_before_novnc "$s" 2>/dev/null || rc=$?
      endsess "$s"
      [[ "$got" == "[::]:$port pid=$pid comm=python3 ino=$ino" ]] && (( rc != 0 ))
    }
    t 'REAL IPv6 wildcard listener: found in real tcp6 and real ss, startup aborted' real_v6_wildcard
    real_v6_loopback() {
      local s pid port ino got; s=$(newsess); export C1_HARNESS_SESSION=$s
      read -r pid port ino < <(listen_on "$s" realv6lo ::1 spawn)
      got=$(c1_harness_listeners "$s"); endsess "$s"
      [[ "$got" == "[::1]:$port pid=$pid comm=python3 ino=$ino" ]]
    }
    t 'REAL IPv6 loopback listener attributed as [::1]' real_v6_loopback
  else
    skip 'REAL IPv6 wildcard/loopback sockets (runner has no IPv6; fixture-backed IPv6 cases above still ran)'
  fi

  # ---- fail-open regressions (M-3 POC) --------------------------------------
  ss_fails_with_real_wildcard() {   # review POC: real marked 0.0.0.0 listener + failing ss gave [] rc=0
    local s rc=0 st; s=$(newsess); export C1_HARNESS_SESSION=$s
    listen_on "$s" wild 0.0.0.0 spawn >/dev/null
    C1_SS_BIN="$ADV/bin/ss"; export FAKE_SS_MODE=fail
    c1_assert_no_listeners_before_novnc "$s" 2>/dev/null || rc=$?
    st=$(sed -n 1p "$s/listener-state"); endsess "$s"
    (( rc != 0 )) && [[ "$st" == failed-unavailable ]]
  }
  t 'M-3 POC: real wildcard listener + failing ss -> fail closed (was: empty set, rc 0)' ss_fails_with_real_wildcard
  ss_blind_to_harness_v6() {   # kernel shows the harness [::] socket, ss omits it
    local s pid port ino d="$ADV/blind6" rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" hid 127.0.0.1 spawn)
    fixture_net "$d"; move_to_v6 "$d" "$ino" 5900 00000000000000000000000000000000
    C1_PROC_NET=$d C1_SS_BIN="$ADV/bin/ss"; export FAKE_SS_MODE=rewrite FAKE_SS_SED="/ ino:$ino /d"
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'ss blind to a harness IPv6 listener the kernel reports: inconsistent evidence, fail closed' ss_blind_to_harness_v6
  kernel_blind_to_harness() {   # ss reports a harness listener, kernel tables omit it
    local s pid port ino d="$ADV/blindk" rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" hidk 127.0.0.1 spawn)
    fixture_net "$d"; awk -v ino="$ino" 'NR == 1 || $10 != ino' /proc/net/tcp >"$d/tcp"
    C1_PROC_NET=$d
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'kernel table missing a harness listener that ss reports: fail closed' kernel_blind_to_harness
  addr_mismatch() {
    local s pid port ino rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" mm 127.0.0.1 spawn)
    C1_SS_BIN="$ADV/bin/ss"; export FAKE_SS_MODE=rewrite FAKE_SS_SED="s/127\.0\.0\.1:$port /127.0.0.1:1 /"
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'kernel and ss disagree on a harness socket address: fail closed' addr_mismatch
  malformed_kernel_row() {
    local s d="$ADV/malk" rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    fixture_net "$d"; printf '   9: ZZZZ:0000 00000000:0000 0A 0 0 0 0 0 1\n' >>"$d/tcp"; C1_PROC_NET=$d
    c1_assert_no_listeners_before_novnc "$s" 2>/dev/null || rc=$?
    endsess "$s"; (( rc != 0 ))
  }
  t 'malformed kernel table row: pre-noVNC check fails closed' malformed_kernel_row

  # ---- attribution beyond the marker (L-1/L-2) -------------------------------
  unmarked_child_attributed() {   # a child that dropped the marker (env -i) keeps its listener attributable
    local s got port; s=$(newsess); export C1_HARNESS_SESSION=$s
    c1_spawn "$s" parent bash -c 'env -i "$1" -I -c "import socket,time;s=socket.socket();s.bind((\"0.0.0.0\",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)" & wait' _ "$PY3" >/dev/null
    for (( i = 0; i < 100; i++ )); do [[ -s "$s/log/parent.log" ]] && break; sleep 0.05; done
    read -r port <"$s/log/parent.log"
    got=$(c1_harness_listeners "$s"); endsess "$s"
    [[ "$got" == "0.0.0.0:$port pid="*" comm=python3 ino="* ]]
  }
  t 'listener held by an unmarked child (env -i) of a harness process is still attributed' unmarked_child_attributed
  leader_dropped_marker() {   # the recorded leader itself exec()s into an env-cleared listener: inconsistent, fail closed
    local s rc=0 i; s=$(newsess); export C1_HARNESS_SESSION=$s
    c1_spawn "$s" execer bash -c 'exec env -i "$1" -I -c "import socket,time;s=socket.socket();s.bind((\"0.0.0.0\",0));s.listen();print(1,flush=True);time.sleep(60)"' _ "$PY3" >/dev/null
    for (( i = 0; i < 100; i++ )); do [[ -s "$s/log/execer.log" ]] && break; sleep 0.05; done
    c1_assert_no_listeners_before_novnc "$s" 2>/dev/null || rc=$?
    local st lp vc=0; st=$(sed -n 1p "$s/listener-state"); lp=$(awk '{print $1; exit}' "$s/pids"); endsess "$s"
    # L-A: the launcher cannot prove ownership (no marker), but the leader is its
    # supervisor's own child: the supervisor terminates it, and only then does
    # verification succeed.
    c1_verify_cleanup "$s" 2>/dev/null || vc=$?
    local gone=0; alive "$lp" || gone=1
    kill "$lp" 2>/dev/null || true; sleep 0.2
    (( rc != 0 )) && [[ "$st" == failed-unavailable ]] && (( vc == 0 && gone == 1 ))
  }
  t 'recorded leader that exec()s into an env-cleared listener: fail closed; its supervisor terminates it before verification' leader_dropped_marker
  setsid_grandchild_attributed() {   # like fluxbox -> fbsetbg: the child calls setsid AND drops the marker
    local s got port; s=$(newsess); export C1_HARNESS_SESSION=$s
    c1_spawn "$s" fluxlike bash -c 'setsid env -i "$1" -I -c "import socket,time;s=socket.socket();s.bind((\"0.0.0.0\",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)" & wait' _ "$PY3" >/dev/null
    for (( i = 0; i < 100; i++ )); do [[ -s "$s/log/fluxlike.log" ]] && break; sleep 0.05; done
    read -r port <"$s/log/fluxlike.log"
    got=$(c1_harness_listeners "$s"); endsess "$s"
    local gp=${got#* pid=}; gp=${gp%% *}; [[ "$gp" =~ ^[0-9]+$ ]] && kill "$gp" 2>/dev/null || true
    [[ "$got" == "0.0.0.0:$port pid="* ]]
  }
  t 'listener of a setsid+unmarked grandchild of a harness process is attributed (process tree)' setsid_grandchild_attributed
  inherited_fd_closed() {   # L-1: a parent listening socket is never inherited by c1_spawn children
    local s got fds; s=$(newsess); export C1_HARNESS_SESSION=$s
    python3 -I -c 'import socket,os,sys
s=socket.socket(); s.bind(("127.0.0.1",0)); s.listen()
os.dup2(s.fileno(), 37); os.set_inheritable(37, True)
os.execvp("bash", ["bash","-c","""source "$1"; pid=$(c1_spawn "$2" child sleep 30)
for i in $(seq 100); do [[ "$(cat /proc/$pid/comm 2>/dev/null)" == sleep ]] && break; sleep 0.05; done
ls -l /proc/$pid/fd/ > "$2/child-fds"; c1_harness_listeners "$2" > "$2/got"; echo "$$" > "$2/parent"; kill $pid""", "_", sys.argv[1], sys.argv[2]])' "$LIB" "$s"
    got=$(cat "$s/got"); fds=$(cat "$s/child-fds"); local parent; parent=$(cat "$s/parent"); endsess "$s"
    # The child holds no socket; the inherited listener IS attributed, to the parent that holds it.
    [[ "$fds" != *socket:* ]] && [[ "$got" =~ ^127\.0\.0\.1:[0-9]+\ pid=$parent\ comm=bash\ ino=[0-9]+$ ]]
  }
  t 'L-1: c1_spawn children do not inherit a parent listening socket (fd 37); the holder is attributed' inherited_fd_closed
  orphan_reported() {   # L-2: a marked leftover (not spawned by the desktop) is named in the report
    local s pid i; s=$(newsess); export C1_HARNESS_SESSION=$s
    ( setsid env C1_HARNESS_SESSION="$s" sh -c 'echo $$ >"$1/orphan.pid"; exec sleep 30' _ "$s" </dev/null >/dev/null 2>&1 & )
    for (( i = 0; i < 100; i++ )); do [[ -s "$s/orphan.pid" ]] && break; sleep 0.05; done
    pid=$(cat "$s/orphan.pid"); sleep 0.1
    local o; o=$(c1_harness_orphans "$s"); endsess "$s"
    [[ "$o" == *"pid=$pid comm=sleep"* ]]
  }
  t 'L-2: marked process not started by the desktop is reported as an orphan' orphan_reported
  recorded_no_marker() {   # recorded live pid whose marker cannot be verified -> fail closed
    local s pid rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    env -u C1_HARNESS_SESSION sleep 30 & pid=$!
    printf '%s %s\n' "$pid" "$(c1_proc_start "$pid")" >>"$s/pids"
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    kill "$pid" 2>/dev/null; endsess "$s"; (( rc != 0 ))
  }
  t 'recorded live harness pid without a verifiable marker: inconsistent process state, fail closed' recorded_no_marker
  malformed_record() {
    local s rc=0 cleanup_rc=0 reg; s=$(newsess); export C1_HARNESS_SESSION=$s
    printf 'garbage line here\n' >>"$s/pids"
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    endsess "$s"
    reg="$ROOT/.${s##*/}.pids"
    # An invalid PID record must survive cleanup and block bulk recovery.
    # The fixture must then retire ONLY the invalid bytes it manufactured;
    # otherwise a later, unrelated bulk-cleanup regression fails spuriously.
    [[ -s "$reg" ]] && grep -Fxq 'garbage line here' "$reg" || return 1
    c1_cleanup_all "$ROOT" 2>/dev/null || cleanup_rc=$?
    (( rc != 0 && cleanup_rc != 0 )) && [[ -s "$reg" ]] || return 1
    rm -f -- "$reg"
    c1_cleanup_all "$ROOT" && [[ ! -e "$reg" ]]
  }
  t 'malformed harness pid record: fail closed' malformed_record
  if (( $(id -u) == 0 )) && command -v setpriv >/dev/null; then
    unreadable_evidence() {   # REAL permission denial: an unprivileged scanner cannot read a root harness process
      local d; d=$(mktemp -d /tmp/c1-unread.XXXXXX); chmod 755 "$d"; cp "$LIB" "$d/lib.sh"; chmod 644 "$d/lib.sh"
      local s="$d/sess"; mkdir -p "$s/log"; chmod 755 "$s"
      env C1_HARNESS_SESSION="$s" sleep 30 & local pid=$!
      sleep 0.1; printf '%s %s\n' "$pid" "$(c1_proc_start "$pid")" >"$s/pids"; chmod 644 "$s/pids"
      local r1=0 r2=0
      setpriv --reuid=65534 --regid=65534 --clear-groups bash -c 'source "$1"; c1_harness_listeners "$2"' _ "$d/lib.sh" "$s" >/dev/null 2>&1 || r1=$?
      setpriv --reuid=65534 --regid=65534 --clear-groups bash -c 'source "$1"; c1_pid_socket_inodes "$2" "$(c1_proc_start "$2")"' _ "$d/lib.sh" "$pid" >/dev/null 2>&1 || r2=$?
      kill "$pid"; rm -rf "$d"
      (( r1 != 0 && r2 != 0 ))
    }
    t 'unreadable marker / fd table of a live harness process (real EACCES): fail closed' unreadable_evidence
  else skip 'real EACCES evidence denial (needs root + setpriv)'; fi

  # ---- ports, teardown and cleanup (L-3) ------------------------------------
  port_free_fail_closed() {
    local rc=0 p; p=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
    C1_SS_BIN="$ADV/bin/ss"; FAKE_SS_MODE=fail c1_port_free "$p" || rc=$?
    (( rc == 2 ))
  }
  t 'c1_port_free: failing ss -> 2 (unknown), never "free"' port_free_fail_closed
  port_free_sees_v6() {
    local d="$ADV/pf6" rc=0 p=45991; fixture_net "$d"
    [[ -e "$d/tcp6" ]] || printf '%s\n' "$TCP6_HDR" >"$d/tcp6"
    v6row 91 00000000000000000000000000000000 "$(hex4 $p)" 999999 >>"$d/tcp6"
    # shellcheck disable=SC2034  # read by the sourced library
    C1_PROC_NET=$d; c1_port_free "$p" || rc=$?
    (( rc != 0 ))
  }
  t 'c1_port_free: an IPv6-only listener on the port (kernel table) means NOT free' port_free_sees_v6
  port_free_real() { local p; p=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'); c1_port_free "$p"; }
  t 'c1_port_free: genuinely free port with full evidence -> free' port_free_real
  teardown_unverifiable() {   # privacy teardown must not report success without evidence
    local s pid port ino rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" ws 127.0.0.1 spawn)
    C1_SS_BIN="$ADV/bin/ss"; export FAKE_SS_MODE=fail
    c1_teardown_novnc "$s" "$port" "$pid" "$(c1_proc_start "$pid")" test 2>/dev/null || rc=$?
    local pf; pf=$(cat "$s/privacy-failed"); unset FAKE_SS_MODE; C1_SS_BIN=ss; endsess "$s"
    (( rc != 0 )) && [[ "$pf" == *teardown-failed* ]]
  }
  t 'noVNC teardown with unavailable listener evidence records teardown-failed (fail closed)' teardown_unverifiable
  cleanup_inode_survivor() {   # a recorded harness listener inode that survives cleanup FAILS verification
    local s fpid fino rc=0 reg; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r fpid _ fino < <(listen_on "$s" survivor 127.0.0.1 foreign)
    printf '%s\n' "$fino" >>"$s/listen-inodes"
    unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
    reg="$ROOT/.${s##*/}.inodes"; [[ -s "$reg" ]]
    c1_verify_cleanup "$s" 2>/dev/null || rc=$?
    kill "$fpid"; sleep 0.2
    (( rc != 0 )) && [[ -e "$reg" ]] && c1_verify_cleanup "$s" && [[ ! -e "$reg" ]]
  }
  t 'L-3: cleanup verification fails while a recorded listener inode still listens; passes once closed' cleanup_inode_survivor
  cleanup_no_evidence() {
    local s rc=0; s=$(newsess); unset C1_HARNESS_SESSION; c1_cleanup_session "$ROOT" "$s"
    # shellcheck disable=SC2034  # read by the sourced library
    C1_SS_BIN="$ADV/bin/ss"; FAKE_SS_MODE=fail c1_verify_cleanup "$s" 2>/dev/null || rc=$?
    (( rc != 0 ))
  }
  t 'cleanup verification with unavailable listener evidence is a FAILURE' cleanup_no_evidence

  inode_record_write_denied() {  # a real owned listener cannot be declared clean when its inode cannot be recorded
    local s pid port ino rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" inode-write 127.0.0.1 spawn)
    mkdir "$s/listen-inodes"  # redirection fails even when a real listener was observed
    c1_harness_listeners "$s" >/dev/null 2>&1 || rc=$?
    rmdir "$s/listen-inodes"
    endsess "$s"
    (( rc != 0 ))
  }
  t 'F-1: unwritable listener-inode record fails closed against a real harness socket' inode_record_write_denied

  inode_preserve_denied() {  # cleanup may kill processes, but must retain source evidence when archival fails
    local s rc=0; s=$(newsess)
    printf '123456789\n' >"$s/listen-inodes"
    mkdir "$ROOT/.${s##*/}.inodes"  # cannot append archived inodes to a directory
    c1_cleanup_session "$ROOT" "$s" 2>/dev/null || rc=$?
    (( rc != 0 )) && [[ -d "$s" && -s "$s/listen-inodes" ]] || return 1
    rmdir "$ROOT/.${s##*/}.inodes"
    c1_cleanup_session "$ROOT" "$s" && c1_verify_cleanup "$s"
  }
  t 'F-1: failed inode preservation retains session evidence and requires verified retry' inode_preserve_denied

  cleanup_all_retains_survivor() {  # bulk recovery must not delete records of a surviving nonstandard listener
    local s pid port ino rc=0 reg; s=$(newsess); export C1_HARNESS_SESSION=$s
    read -r pid port ino < <(listen_on "$s" bulk-survivor 127.0.0.1 foreign)
    printf '%s\n' "$ino" >"$s/listen-inodes"
    unset C1_HARNESS_SESSION
    c1_cleanup_all "$ROOT" 2>/dev/null || rc=$?
    reg="$ROOT/.${s##*/}.inodes"
    (( rc != 0 )) && [[ -s "$reg" ]] && alive "$pid" || { kill "$pid" 2>/dev/null || true; return 1; }
    kill "$pid"; sleep 0.2
    c1_cleanup_all "$ROOT" && [[ ! -e "$reg" ]]
  }
  t 'F-1: bulk cleanup preserves survivor evidence until listener closure is verified' cleanup_all_retains_survivor


  # ---- startup paths with adversarial desktop binaries ------------------------
  if command -v Xvfb >/dev/null && command -v fluxbox >/dev/null && command -v xauth >/dev/null; then
    mkdir -p "$ADV/evil"
    # An x11vnc stand-in that behaves like the H-1 build: serves the Unix socket,
    # consumes the one-time password file, AND opens a wildcard TCP listener.
    cat >"$ADV/evil/x11vnc" <<'X'
#!/usr/bin/env python3
import os, socket, sys, time
a = sys.argv; sock = a[a.index('-unixsock') + 1]; pw = a[a.index('-passwdfile') + 1]
fam, host = (socket.AF_INET6, '::') if os.environ.get('EVIL_V6') == '1' else (socket.AF_INET, '0.0.0.0')
t = socket.socket(fam); t.bind((host, 0)); t.listen()
u = socket.socket(socket.AF_UNIX); u.bind(sock); u.listen()
if pw.startswith('rm:'): os.unlink(pw[3:])
time.sleep(60)
X
    chmod 755 "$ADV/evil/x11vnc"
    evil_x11vnc_aborts() {
      local s rc=0 err; s=$(newsess); export C1_HARNESS_SESSION=$s
      PATH="$ADV/evil:$PATH"; [[ "$HAVE_V6" == 1 ]] && export EVIL_V6=1
      err=$(c1_start_display_vnc "$s" "$(c1_gen_password)" 2>&1) || rc=$?
      local st; st=$(sed -n 1p "$s/listener-state" 2>/dev/null || true)
      endsess "$s"; c1_verify_cleanup "$s"
      (( rc != 0 )) && [[ "$st" == failed-unexpected ]] && [[ "$err" =~ (0\.0\.0\.0|\[::\]):[0-9]+\ pid=[0-9]+\ comm=[A-Za-z0-9._-]+\ ino=[0-9]+ ]]
    }
    t 'interrupted startup: x11vnc opening a wildcard TCP listener aborts before noVNC; cleanup verified' evil_x11vnc_aborts
    cat >"$ADV/evil/x11vnc-dead" <<'X'
#!/usr/bin/env bash
exit 1
X
    chmod 755 "$ADV/evil/x11vnc-dead"
    dead_x11vnc() {
      local s rc=0; s=$(newsess); export C1_HARNESS_SESSION=$s
      mkdir -p "$ADV/dead"; ln -sf "$ADV/evil/x11vnc-dead" "$ADV/dead/x11vnc"; PATH="$ADV/dead:$PATH"
      c1_start_display_vnc "$s" "$(c1_gen_password)" 2>/dev/null || rc=$?
      endsess "$s"; c1_verify_cleanup "$s" && (( rc != 0 ))
    }
    t 'interrupted startup: x11vnc dying before its socket appears fails and leaves nothing behind' dead_x11vnc
  else skip 'adversarial x11vnc startup (Xvfb/fluxbox/xauth missing)'; fi
  if command -v websockify >/dev/null; then
    mkdir -p "$ADV/race"
    # T-1: an UNRELATED, unmarked process OUTSIDE the harness tree grabs the
    # noVNC port on the wildcard address between the free check and the bind.
    # The foreign listener is armed by this test shell (not by the supervised
    # stand-in, whose orphans the L-A supervisor would adopt); the stand-in only
    # fires the trigger. Every value the stand-in needs is embedded here: the
    # desktop credential-isolation policy (env -i allowlist) passes it no test
    # variables, and must not be weakened to make this fixture work.
    RACE_TRIGGER="$ADV/race.trigger"
    cat >"$ADV/race/websockify" <<W
#!/usr/bin/env bash
: >$(printf '%q' "$RACE_TRIGGER")
exec sleep 30
W
    chmod 755 "$ADV/race/websockify"
    # shellcheck disable=SC2016
    RACE_ARM='import os,socket,sys,time
trig, pidf, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
open(pidf + ".armed", "w").write(str(os.getpid()))
end = time.time() + 20
while not os.path.exists(trig):
    if time.time() > end: sys.exit(3)
    time.sleep(0.005)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", port)); s.listen()
open(pidf + ".tmp", "w").write(str(os.getpid())); os.rename(pidf + ".tmp", pidf)
time.sleep(30)'
    port_race() {
      local s port rc=0 pf fp='' ok=1 i listening; s=$(newsess); export C1_HARNESS_SESSION=$s
      port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
      local race_pid="$ADV/race.pid"; rm -f "$race_pid" "$race_pid.armed" "$RACE_TRIGGER"
      ( env -i setsid "$PY3" -I -c "$RACE_ARM" "$RACE_TRIGGER" "$race_pid" "$port" </dev/null >"$ADV/race.log" 2>&1 & )
      for (( i=0; i<200; i++ )); do [[ -s "$race_pid.armed" ]] && break; sleep 0.01; done
      [[ -s "$race_pid.armed" ]] || { echo 'fixture: foreign listener was never armed'; return 1; }
      PATH="$ADV/race:$PATH" c1_start_websockify "$s" "$port" >/dev/null 2>&1 || rc=$?
      pf=$(cat "$s/privacy-failed" 2>/dev/null || true)
      # Fail closed if the fixture could not establish the race: the foreign
      # wildcard listener must exist, be unmarked, and lie outside the harness tree.
      [[ -s "$race_pid" ]] && fp=$(cat "$race_pid") || { echo 'fixture: foreign wildcard listener never started'; ok=0; }
      if (( ok )); then
        listening=$("$REAL_SS" -Hltn "sport = :$port" 2>/dev/null || true)
        [[ "$listening" == *"0.0.0.0:$port"* ]] || { echo "fixture: no 0.0.0.0:$port listener (ss: ${listening:-none})"; ok=0; }
        if c1_pid_has_marker "$fp" "$s"; then echo 'fixture: race listener carries the session marker'; ok=0; fi
        c1_load_tree "$s"
        if c1_tree_attributed "$fp"; then echo 'fixture: race listener is inside the harness tree'; ok=0; fi
      fi
      [[ -n "$fp" ]] && kill "$fp" 2>/dev/null
      sleep 0.2; endsess "$s"
      (( ok )) && (( rc == 2 )) && [[ "$pf" == *teardown-failed* ]] || { echo "rc=$rc privacy-failed=${pf:-none}"; return 1; }
    }
    port_race_blind() {   # negative control: a race that never fires must FAIL the case, not pass silently
      local RACE_ARM='import sys; open(sys.argv[2] + ".armed", "w").write("1")'
      port_race
    }
    t 'port-listener race: foreign wildcard listener takes the noVNC port -> rejected, teardown-failed (rc 2)' port_race
    f 'T-1 control: a port race fixture that never establishes the foreign listener fails the case' port_race_blind
  else skip 'noVNC port race (websockify missing)'; fi

  # ---- real binaries: no IPv4/IPv6 TCP socket is even attempted (M-1) -------
  if command -v strace >/dev/null && strace -qq -e trace=socket true 2>/dev/null && command -v Xvfb >/dev/null && command -v x11vnc >/dev/null && command -v xauth >/dev/null; then
    # strace_desktop MODE -> run REAL Xvfb + x11vnc with the exact harness argv
    # (MODE=shipped strips -rfbportv6 0 / -no6, the ee6ded8 flags) under strace;
    # traces in $ADV/strace.MODE.{xvfb,vnc}. rc 0 once the VNC Unix socket was served.
    strace_desktop() {
      local mode=$1 s disp tr="$ADV/strace.$1" i ok=1 skipn=0 a; local -a cmd c2=()
      s=$(newsess); disp=$(c1_free_display)
      touch "$s/Xauthority"; chmod 600 "$s/Xauthority"; xauth -q -f "$s/Xauthority" add "$disp" . "$(mcookie)"
      mapfile -t cmd < <(c1_cmd_xvfb "$disp" "$s")
      strace -f -qq -e trace=socket -o "$tr.xvfb" "${cmd[@]}" >/dev/null 2>&1 & local xp=$!
      for (( i = 0; i < 50; i++ )); do [[ -S "/tmp/.X11-unix/X${disp#:}" ]] && break; sleep 0.1; done
      printf 'pw\n' >"$s/vncpass"
      mapfile -t cmd < <(c1_cmd_x11vnc "$disp" "$s")
      if [[ $mode == shipped ]]; then
        for a in "${cmd[@]}"; do
          if (( skipn )); then skipn=0; continue; fi
          [[ $a == -rfbportv6 ]] && { skipn=1; continue; }
          [[ $a == -no6 ]] && continue
          c2+=("$a")
        done
        cmd=("${c2[@]}")
      fi
      strace -f -qq -e trace=socket -o "$tr.vnc" "${cmd[@]}" >/dev/null 2>&1 & local vp=$!
      for (( i = 0; i < 50; i++ )); do [[ -S "$s/vnc.sock" ]] && { ok=0; break; }; sleep 0.1; done
      sleep 0.5
      # Killing strace only detaches it: stop the traced programs themselves first.
      pkill -KILL -P "$vp" 2>/dev/null || true; pkill -KILL -P "$xp" 2>/dev/null || true
      kill "$vp" "$xp" 2>/dev/null || true; wait "$vp" "$xp" 2>/dev/null || true
      rm -rf "$s"
      return "$ok"
    }
    no_tcp_attempt() { strace_desktop fixed && [[ -s "$ADV/strace.fixed.vnc" ]] && ! grep -hE 'socket\(AF_INET6?, SOCK_STREAM' "$ADV/strace.fixed.xvfb" "$ADV/strace.fixed.vnc"; }
    shipped_v6_attempt() { strace_desktop shipped && grep -qE 'socket\(AF_INET6, SOCK_STREAM' "$ADV/strace.shipped.vnc"; }
    t 'M-1: real Xvfb + x11vnc with the exact harness argv attempt NO IPv4/IPv6 TCP socket (strace)' no_tcp_attempt
    t 'M-1 control: the ee6ded8 x11vnc flags DO attempt an IPv6 TCP socket (the probe is not blind)' shipped_v6_attempt
  else skip 'strace syscall check of real Xvfb/x11vnc (strace/Xvfb/x11vnc missing)'; fi
else
  skip 'IPv4/IPv6 listener evidence integration (ss/python3 missing)'
fi

echo '# pinned PR #60 integration (real repository history)'
if [[ -n "${TOP:=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)}" ]] && git -C "$TOP" cat-file -e "$C1_PW_SHA_APPROVED^{commit}" 2>/dev/null \
   && git -C "$TOP" cat-file -e "$C1_AUTH_SHA_APPROVED^{commit}" 2>/dev/null; then
  t 'pinned AUTH is an ancestor of PW and the allowlist equals the real diff exactly (never widened)' \
    c1_verify_commits "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES"
  pinned_r13() {   # B-1: the pinned register clears App.tsx only at its PR #60-certified blob; nothing is pending
    local o
    [[ "$C1_PROD_REVIEW_PENDING" == '' ]]
    [[ "$C1_PROD_CERTIFIED" == 'frontend/src/App.tsx@a0b753b533165bb12bc516478c8036645f2f292a' ]]
    [[ "$(git -C "$TOP" rev-parse "$C1_PW_SHA_APPROVED:frontend/src/App.tsx")" == a0b753b533165bb12bc516478c8036645f2f292a ]]
    [[ "$(git -C "$TOP" diff --no-renames --name-only "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" | grep -vE "$C1_TEST_INFRA_RE")" == frontend/src/App.tsx ]]
    [[ ! frontend/src/App.tsx =~ $C1_TEST_INFRA_RE ]]
    o=$(c1_verify_prod_pending "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES" "$C1_PROD_REVIEW_PENDING" "$C1_PROD_CERTIFIED")
    [[ "$o" == none ]]
  }
  t 'pinned diff: App.tsx is the only production file and is certified at exact blob a0b753b; nothing pending (R13 none)' pinned_r13
  pinned_r13_altered() {   # a deliberately altered certified blob for the real App.tsx fails closed
    local alt=${C1_PROD_CERTIFIED%?}; [[ "${C1_PROD_CERTIFIED: -1}" == a ]] && alt+=b || alt+=a
    r13_rejects 'certified file frontend/src/App\.tsx blob a0b753b53316 does not match PW blob a0b753b53316; re-certification required' \
      "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES" "$C1_PROD_REVIEW_PENDING" "$alt"
    r13_rejects 'production file frontend/src/App\.tsx is allowlisted but neither' \
      "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES" '' ''
    r13_rejects 'frontend/src/App\.tsx is listed more than once \(pending-review and certified\)' \
      "$TOP" "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" "$C1_AUTH_TO_PW_FILES" 'frontend/src/App.tsx' "$C1_PROD_CERTIFIED"
  }
  t 'pinned diff: an altered certified App.tsx blob, a missing clearance, or a pending+certified overlap all fail closed' pinned_r13_altered
  t 'Entra auth modules are byte-identical between the auth baseline and the tested head' \
    git -C "$TOP" diff --quiet "$C1_AUTH_SHA_APPROVED" "$C1_PW_SHA_APPROVED" -- frontend/src/entraAuth.ts frontend/src/entraAuthCore.ts frontend/package-lock.json
  pinned_vite_guard() {
    local d="$TMP/pinvite" f; mkdir -p "$d/frontend"
    for f in vite.config.ts playwright.config.mjs package.json; do git -C "$TOP" show "$C1_PW_SHA_APPROVED:frontend/$f" >"$d/frontend/$f"; done
    [[ -z "$(git -C "$TOP" ls-tree --name-only "$C1_PW_SHA_APPROVED" frontend/ | grep -E 'vite\.config\.' | grep -v '^frontend/vite.config.ts$')" ]]
    c1_check_vite_host_guard "$d"
  }
  t 'the pinned PR #60 Vite/Playwright configuration passes the 5173 host-guard check (L-11)' pinned_vite_guard
  pinned_webrtc_policy() {
    local d="$TMP/pinwebrtc" f
    mkdir -p "$d/frontend/e2e" "$d/harness/supplemental"
    for f in e2e/c1-webrtc-chromium.sh e2e/c1-webrtc-policy.mjs e2e/c1-webrtc-udp-check.mjs playwright.config.mjs; do
      git -C "$TOP" show "$C1_PW_SHA_APPROVED:frontend/$f" >"$d/frontend/$f"
    done
    chmod +x "$d/frontend/e2e/c1-webrtc-chromium.sh"
    cp "$HERE/../supplemental/playwright.supplemental.config.mjs" "$d/harness/supplemental/"
    c1_check_webrtc_policy "$d" "$d/harness"
    sed -i 's/launchOptions: live ? headedWebrtcLaunchOptions/launchOptions: live ? {}/' "$d/frontend/playwright.config.mjs"
    ! c1_check_webrtc_policy "$d" "$d/harness"
  }
  t 'L-2 pinned browser policies and STUN positive/zero controls are required; dropped flag fails closed' pinned_webrtc_policy
else skip 'pinned PR #60 integration (pinned commits not in local history)'; fi

echo '# harness diff scope after trusted-main workflow reconciliation'
TOP=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || true)
# This is the separate PR #60 trusted-main *integration* baseline. It is NOT
# the pinned C1 test-suite head and must never widen C1_AUTH_TO_PW_FILES.
C1_PR60_WORKFLOW_BASE='f4f1066af0a0def2b595a14c211341c27733064c'
if [[ -n "$TOP" ]] && git -C "$TOP" cat-file -e "$C1_PR60_WORKFLOW_BASE^{commit}" 2>/dev/null \
   && git -C "$TOP" merge-base --is-ancestor "$C1_PR60_WORKFLOW_BASE" HEAD 2>/dev/null; then
  pinned_l2_scope() {
    local actual expected
    actual=$(git -C "$TOP" diff --no-renames --name-only "$C1_PR60_WORKFLOW_BASE" HEAD | grep -v '^tools/c1-harness/' | LC_ALL=C sort)
    expected=$(printf '%s\n' '.github/workflows/frontend-quality-gates.yml' 'frontend/e2e/c1-webrtc-chromium.sh' 'frontend/e2e/c1-webrtc-policy.mjs' 'frontend/e2e/c1-webrtc-udp-check.mjs' 'frontend/playwright.config.mjs' | LC_ALL=C sort)
    [[ "$actual" == "$expected" ]]
  }
  t 'L-2 only changes approved frontend policy/probe/config/workflow plus harness' pinned_l2_scope
  t 'approved frontend suite is byte-identical to clean L-2 checkpoint at HEAD' \
    git -C "$TOP" diff --quiet "$C1_PW_SHA_APPROVED" HEAD -- frontend
  t 'trusted PR60 backend/scripts/tests remain unchanged by L-2 integration' \
    git -C "$TOP" diff --quiet "$C1_PR60_WORKFLOW_BASE" HEAD -- backend scripts tests
else skip 'diff scope (trusted workflow base not in local history)'; fi

echo
echo "c1 harness shell tests: $PASSED passed, $FAILED failed, $SKIPPED skipped"
(( FAILED == 0 ))

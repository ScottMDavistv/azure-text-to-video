#!/usr/bin/env bash
# shellcheck disable=SC2034  # Fixture variables are consumed by extracted entry points.
# Real launcher/cleaner processes with local fixtures and no sockets or credentials.
set -Eeuo pipefail
umask 077
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
# shellcheck source=../lib/c1-lib.sh
source "$HERE/lib/c1-lib.sh"

wait_for_file() {
  local path=$1 step
  for ((step=0; step<150; step++)); do
    [[ ! -f "$path" ]] || return 0
    sleep 0.02
  done
  return 1
}

cat >"$TMP/entry.sh" <<'ENTRY'
set -Eeuo pipefail
base=$1 ROOT=$2 fixture=$3; shift 3
source "$base/lib/c1-lib.sh"
eval "$(sed -n '/^main() {$/,/^}$/p' "$base/c1-session.sh")"
c1_port_free() { return 0; }  # socket verification has its own integration tests
session() {
  c1_recover_before_start "$ROOT" || return 1
  session_path=$(c1_session_new "$ROOT" "$$") || return 1
  c1_start_watchdog "$ROOT" "$session_path" "$$" "$base/lib/c1-lib.sh" || return 1
  trap 'c1_cleanup_session "$ROOT" "$session_path"; c1_verify_cleanup "$session_path"' EXIT
  trap 'exit 130' TERM
  if [[ -f "$fixture/spawn-victim" ]]; then
    c1_spawn "$session_path" victim sleep 60 >"$fixture/victim" || return 1
  fi
  printf '%s\n' "$session_path" >"$fixture/ready"
  while [[ ! -e "$fixture/release" ]]; do sleep 0.02; done
}
if [[ -f "$fixture/pause-cleanup" ]]; then
  eval "$(declare -f _c1_cleanup_session | sed '1s/_c1_cleanup_session/fixture_cleanup/')"
  _c1_cleanup_session() {
    printf '%s\n' "$$" >>"$fixture/entries"
    while [[ ! -e "$fixture/release" ]]; do sleep 0.02; done
    fixture_cleanup "$@"
  }
fi
main "$@"
ENTRY

entry() { bash "$TMP/entry.sh" "$BASE" "$STATE" "$FIXTURE" "$@"; }

competing_launchers() (
  entry run >"$FIXTURE/first.log" 2>&1 &
  local first=$!
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/ready" || return 1
  local original
  original=$(cat "$STATE/active") || return 1
  if entry run >"$FIXTURE/second.log" 2>&1; then return 1; fi
  grep -q 'cannot acquire the C1 state lock' "$FIXTURE/second.log" || return 1
  [[ "$(cat "$STATE/active")" == "$original" ]] || return 1
  touch "$FIXTURE/release"
  wait "$first" || return 1
  [[ ! -e "$STATE/active" ]]
)

overlapping_cleaners() (
  mkdir -p "$STATE/session.fixture"
  printf '999999 1 %s/session.fixture\n' "$STATE" >"$STATE/active"
  chmod 600 "$STATE/active"
  : >"$FIXTURE/pause-cleanup"
  entry cleanup >"$FIXTURE/first.log" 2>&1 &
  local first=$!
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/entries" || return 1
  # Release the paused cleaner even if a mutant admits the second process.
  local second result=0
  entry cleanup >"$FIXTURE/second.log" 2>&1 & second=$!
  for _ in {1..100}; do
    if ! kill -0 "$second" 2>/dev/null || [[ $(wc -l <"$FIXTURE/entries") != 1 ]]; then break; fi
    sleep 0.02
  done
  touch "$FIXTURE/release"
  wait "$second" || result=$?
  wait "$first" || return 1
  [[ "$result" != 0 && $(wc -l <"$FIXTURE/entries") == 1 && ! -e "$STATE/active" ]] &&
    grep -q 'cannot acquire the C1 state lock' "$FIXTURE/second.log"
)

malformed_active() {
  mkdir -p "$STATE/session.fixture"
  printf '999999 1 %s/session.fixture\nextra\n' "$STATE" >"$STATE/active"
  chmod 600 "$STATE/active"
  if entry cleanup >"$FIXTURE/cleanup.log" 2>&1; then return 1; fi
  [[ -e "$STATE/active" && -d "$STATE/session.fixture" ]]
}

active_publication_preserves_owner() (
  source "$BASE/lib/c1-lib.sh"
  local owner=$BASHPID original session_path
  session_path=$(c1_session_new "$STATE" "$owner") || return 1
  original=$(cat "$STATE/active") || return 1
  if c1_session_new "$STATE" "$owner" >/dev/null 2>&1; then return 1; fi
  [[ "$(cat "$STATE/active")" == "$original" && -d "$session_path" ]]
)

verification_is_retried() (
  source "$BASE/lib/c1-lib.sh"
  local session_path="$STATE/session.fixture" reject="$FIXTURE/reject"
  mkdir -p "$session_path"
  : >"$reject"
  c1_port_free() { [[ ! -e "$reject" ]]; }
  if c1_cleanup_all "$STATE" >/dev/null 2>&1; then return 1; fi
  [[ ! -e "$session_path" && -f "$STATE/.session.fixture.pending" ]] || return 1
  if c1_cleanup_all "$STATE" >/dev/null 2>&1; then return 1; fi
  rm -f -- "$reject"
  c1_cleanup_all "$STATE" && [[ ! -e "$STATE/.session.fixture.pending" ]]
)

retained_privacy_failure() (
  source "$BASE/lib/c1-lib.sh"
  local session_path="$STATE/session.fixture"
  mkdir -p "$session_path"
  c1_port_free() { return 0; }
  c1_teardown_novnc "$session_path" 6080 '' '' public || return 1
  local register
  register=$(c1_privacy_register "$session_path") || return 1
  grep -qs ' public$' "$register" || return 1
  entry cleanup || return 1
  [[ ! -e "$session_path" && -s "$register" ]] && c1_privacy_failed "$session_path"
)

reused_owner() (
  source "$BASE/lib/c1-lib.sh"
  local victim process_start session_path="$STATE/session.fixture"
  sleep 30 & victim=$!
  trap 'kill "$victim" 2>/dev/null || true; wait "$victim" 2>/dev/null || true' EXIT
  process_start=$(c1_proc_start "$victim") || return 1
  mkdir -p "$session_path"
  printf '%s %s %s\n' "$victim" "$((process_start+1))" "$session_path" >"$STATE/active"
  chmod 600 "$STATE/active"
  c1_record_privacy_failure "$session_path" teardown-failed || return 1
  ( c1_port_free() { return 0; }; c1_watchdog_loop "$STATE" "$session_path" "$victim" "$((process_start+1))" ) || return 1
  [[ ! -e "$session_path" ]] && kill -0 "$victim"
)

watchdog_waits_for_launcher() (
  : >"$FIXTURE/pause-cleanup"
  entry run >"$FIXTURE/launcher.log" 2>&1 &
  local first=$!
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/ready" || return 1
  local session_path result=0
  session_path=$(cat "$FIXTURE/ready") || return 1
  c1_record_privacy_failure "$session_path" teardown-failed || return 1
  wait_for_file "$FIXTURE/entries" || return 1
  sleep 0.4
  [[ -d "$session_path" && -f "$STATE/active" ]] || return 1
  touch "$FIXTURE/release"
  wait "$first" || result=$?
  [[ "$result" == 130 && ! -e "$session_path" && ! -e "$STATE/active" ]] &&
    c1_privacy_failed "$session_path"
)

launcher_crash_recovery() (
  : >"$FIXTURE/spawn-victim"
  entry run >"$FIXTURE/launcher.log" 2>&1 &
  local first=$! owner session_path victim step
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/ready" || return 1
  read -r owner _ session_path <"$STATE/active" || return 1
  victim=$(cat "$FIXTURE/victim") || return 1
  kill -KILL "$owner" || return 1
  wait "$first" 2>/dev/null || true
  for ((step=0; step<80; step++)); do
    if [[ ! -e "$session_path" && ! -e "$STATE/active" ]] && ! kill -0 "$victim" 2>/dev/null; then return 0; fi
    sleep 0.05
  done
  c1_signal_owned TERM "$victim" "$session_path" || true
  return 1
)

r11_exit_status_and_late_privacy() (
  source "$BASE/lib/c1-lib.sh"
  eval "$(sed -n '/^finish_r11() {$/,/^}$/p' "$BASE/c1-session.sh")"
  local status=0 root_fixture="$STATE" variant
  ROOT="$root_fixture" REPO=fixture HARNESS_REL=fixture R11_HEAD=fixture
  c1_port_free() { return 0; }
  c1_verify_harness_clean() { return 0; }
  git() { printf 'fixture\n'; }
  for variant in clean late interrupted; do
    S="$ROOT/session.$variant"
    mkdir "$S" || return 1
    R11_EVIDENCE="$FIXTURE/$variant.md"
    PORT_PRIVATE=verified-probe-gh
    printf 'verified\n127.0.0.1:6080 pid=11 comm=websockify ino=31\n' >"$S/listener-state"
    if [[ "$variant" == late ]]; then c1_record_privacy_failure "$S" public || return 1; fi
    status=0
    (
      trap 'finish_r11 "$?"' EXIT
      if [[ "$variant" == interrupted ]]; then exit 130; fi
      exit 0
    ) >"$FIXTURE/$variant.log" 2>&1 || status=$?
    case "$variant" in
      clean) [[ "$status" == 0 ]] && grep -qx -- '- R11: PASS' "$R11_EVIDENCE" || return 1 ;;
      late) [[ "$status" == 1 ]] && grep -qx -- '- Port privacy: failed' "$R11_EVIDENCE" || return 1 ;;
      interrupted) [[ "$status" == 130 ]] && grep -qx -- '- R11: FAIL' "$R11_EVIDENCE" || return 1 ;;
    esac
    [[ ! -e "$S" ]] || return 1
  done
)

run_case() {
  BASE=$1 FIXTURE=$(mktemp -d "$TMP/case.XXXXXXXX")
  STATE="$FIXTURE/state"
  mkdir -m 700 "$STATE"
  "$2"
}

# Mutate temporary copies only. Each mutation must violate the same oracle
# that passed against the workspace sources.
if [[ "${1:-}" == --mutations ]]; then
  for spec in 'competing_launchers:lock' 'overlapping_cleaners:lock' \
    'malformed_active:record' 'retained_privacy_failure:privacy' 'reused_owner:pid' \
    'watchdog_waits_for_launcher:watchdog' 'r11_exit_status_and_late_privacy:r11' \
    'launcher_crash_recovery:inherited-lock' 'active_publication_preserves_owner:publication' \
    'verification_is_retried:retry'; do
    case_name=${spec%:*} mutation=${spec#*:}
    mutant=$(mktemp -d "$TMP/mutant.XXXXXXXX")
    mkdir "$mutant/lib"
    cp "$HERE/c1-session.sh" "$mutant/"
    cp "$HERE/lib/"{c1-lib.sh,c1-supervisor.py} "$mutant/lib/"
    case $mutation in
      lock) sed -i '/^      c1_lock_state "\$ROOT"/d; /run|r11-only|preflight|cleanup|purge)/a\      :' "$mutant/c1-session.sh" ;;
      record) sed -i '/\[\[ "\$record" =~ \$pattern \]\]/i\  record=${record%%$'"'"'\\n'"'"'*}$'"'"'\\n'"'"'' "$mutant/lib/c1-lib.sh" ;;
      publication) sed -i 's/ln -T -- "$temp" "$root\/active"/mv -T -- "$temp" "$root\/active"/' "$mutant/lib/c1-lib.sh" ;;
      retry) sed -i 's/"$root"\/.session.\*.pending //' "$mutant/lib/c1-lib.sh" ;;
      privacy) sed -i 's/c1_record_privacy_failure "\$s" "\$reason" || evidence_error=1/:/' "$mutant/lib/c1-lib.sh" ;;
      pid)
        # A numeric-PID-only watchdog signals the unrelated reused PID.
        sed -i '/launcher_state=$(c1_session_status "$root") || return 1/a\    if [[ "$launcher_state" == stale ]]; then kill -TERM "$owner"; return 0; fi' "$mutant/lib/c1-lib.sh"
        ;;
      r11)
        sed -i '/^    \[\[ "\$C1_FINAL_PRIVACY" == clean \]\] || PORT_PRIVATE=failed/d' "$mutant/c1-session.sh"
        ;;
      inherited-lock)
        sed -i 's/( c1_close_inherited_fds; exec "${pre\[@\]}"/( exec "${pre[@]}"/' "$mutant/lib/c1-lib.sh"
        ;;
      watchdog)
        sed -i '/sent=1/a\      c1_cleanup_session "$root" "$session"' "$mutant/lib/c1-lib.sh"
        ;;
    esac
    if run_case "$mutant" "$case_name"; then
      printf 'FAIL: %s mutation escaped %s\n' "$mutation" "$case_name" >&2
      exit 1
    fi
    printf 'DETECTED: %s by %s\n' "$mutation" "$case_name"
  done
else
  for case_name in competing_launchers overlapping_cleaners malformed_active retained_privacy_failure reused_owner watchdog_waits_for_launcher r11_exit_status_and_late_privacy launcher_crash_recovery active_publication_preserves_owner verification_is_retried; do
    if ! run_case "$HERE" "$case_name"; then
      printf 'FAIL: %s\n' "$case_name" >&2
      for log in "$FIXTURE"/*.log; do [[ ! -f "$log" ]] || cat "$log" >&2; done
      exit 1
    fi
    printf 'PASS: %s\n' "$case_name"
  done
fi

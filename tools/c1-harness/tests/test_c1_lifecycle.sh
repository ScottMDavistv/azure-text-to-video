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
  if [[ -f "$fixture/finish-r11" ]]; then
    eval "$(sed -n '/^finish_r11() {$/,/^}$/p' "$base/c1-session.sh")"
    S=$session_path R11_EVIDENCE="$fixture/r11.md" PORT_PRIVATE=verified-probe-gh
    REPO=fixture R11_HEAD=fixture HARNESS_REL=fixture
    git() { printf 'fixture\n'; }
    c1_verify_harness_clean() { return 0; }
    c1_stop_privacy_monitor() { return 0; }
    printf 'verified\n127.0.0.1:6080 pid=11 comm=websockify ino=31\n' >"$S/listener-state"
    trap 'finish_r11 "$?"' EXIT
  else
    trap 'c1_cleanup_launcher "$ROOT" "$session_path"; c1_verify_cleanup "$session_path"' EXIT
  fi
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
  local command
  for command in cleanup purge; do
    if entry "$command" >"$FIXTURE/$command.log" 2>&1; then return 1; fi
    grep -q 'cannot verify C1 launcher state' "$FIXTURE/$command.log" || return 1
  done
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
  c1_stop_privacy_monitor() { [[ "$variant" != stop-failed ]] || return 6; }
  eval "$(declare -f c1_cleanup_launcher | sed '1s/c1_cleanup_launcher/fixture_cleanup_launcher/')"
  eval "$(declare -f c1_verify_cleanup | sed '1s/c1_verify_cleanup/fixture_verify_cleanup/')"
  c1_cleanup_launcher() {
    fixture_cleanup_launcher "$@" || return
    [[ "$variant" != cleanup-failed ]] || return 7
  }
  c1_verify_cleanup() {
    fixture_verify_cleanup "$@" || return
    [[ "$variant" != verify-failed ]] || return 8
  }
  git() { printf 'fixture\n'; }
  for variant in clean late interrupted stop-failed cleanup-failed verify-failed; do
    S=$(c1_session_new "$ROOT" "$$") || return 1
    R11_EVIDENCE="$FIXTURE/$variant.md"
    PORT_PRIVATE=verified-probe-gh
    printf 'verified\n127.0.0.1:6080 pid=11 comm=websockify ino=31\n' >"$S/listener-state"
    if [[ "$variant" == late ]]; then
      c1_record_privacy_failure "$S" public || return 1
      c1_record_monitor_probe_failure "$S" 'unauthenticated probe got HTTP 200' 2>/dev/null &&
        return 1
      c1_record_monitor_probe_failure "$S" 'unauthenticated probe got (HTTP 200)' || return 1
    fi
    status=0
    (
      trap 'finish_r11 "$?"' EXIT
      if [[ "$variant" == interrupted ]]; then exit 130; fi
      exit 0
    ) >"$FIXTURE/$variant.log" 2>&1 || status=$?
    case "$variant" in
      clean) [[ "$status" == 0 ]] && grep -qx -- '- R11: PASS' "$R11_EVIDENCE" || return 1 ;;
      late)
        [[ "$status" == 1 ]] &&
          grep -Fxq -- '- Port privacy: failed' "$R11_EVIDENCE" &&
          grep -Fxq -- '- Monitor probe failure: unauthenticated probe got (HTTP 200)' "$R11_EVIDENCE" || return 1
        ;;
      interrupted) [[ "$status" == 130 ]] && grep -qx -- '- R11: FAIL' "$R11_EVIDENCE" || return 1 ;;
      stop-failed)
        [[ "$status" == 1 ]] &&
          grep -Fxq -- '- Monitor shutdown: FAIL (exit 6)' "$R11_EVIDENCE" &&
          grep -Fxq -- '- Cleanup: PASS' "$R11_EVIDENCE" &&
          grep -Fxq -- '- R11: FAIL' "$R11_EVIDENCE" || return 1
        ;;
      cleanup-failed)
        [[ "$status" == 1 ]] &&
          grep -Fxq -- '- Session cleanup: FAIL (exit 7)' "$R11_EVIDENCE" &&
          grep -Fxq -- '- Post-cleanup verification: not-run' "$R11_EVIDENCE" &&
          grep -Fxq -- '[c1] R11 session cleanup: FAIL (exit 7)' "$FIXTURE/$variant.log" || return 1
        ;;
      verify-failed)
        [[ "$status" == 1 ]] &&
          grep -Fxq -- '- Session cleanup: PASS' "$R11_EVIDENCE" &&
          grep -Fxq -- '- Post-cleanup verification: FAIL (exit 8)' "$R11_EVIDENCE" &&
          grep -Fxq -- '[c1] R11 post-cleanup verification: FAIL (exit 8)' "$FIXTURE/$variant.log" || return 1
        ;;
    esac
    [[ ! -e "$S" ]] || return 1
  done
)

process_is_live() {
  [[ "$(c1_proc_start "$1")" == "$2" && ! "$(c1_proc_stat_field "$1" 1)" =~ ^[ZX]$ ]]
}

wait_for_exit() {
  local step
  for ((step=0; step<100; step++)); do
    if ! process_is_live "$1" "$2"; then return 0; fi
    sleep 0.05
  done
  return 1
}

watchdog_exits_after_cleanup() (
  entry run >"$FIXTURE/launcher.log" 2>&1 &
  local first=$! session_path watchdog start
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/ready" || return 1
  session_path=$(cat "$FIXTURE/ready") || return 1
  read -r watchdog start <"$session_path/tmp/watchdog-ready" || return 1
  process_is_live "$watchdog" "$start" || return 1
  if c1_pid_has_marker "$watchdog" "$session_path"; then return 1; fi
  touch "$FIXTURE/release"
  wait "$first" || return 1
  [[ ! -e "$session_path" && ! -e "$STATE/active" ]] &&
    wait_for_exit "$watchdog" "$start"
)

launcher_ambiguous_record() { ambiguous_owner_teardown launcher; }
watchdog_ambiguous_record() { ambiguous_owner_teardown watchdog; }

ambiguous_owner_teardown() (
  local mode=$1 first owner owner_start session_path victim victim_start
  local other other_start watchdog watchdog_start step status=0
  : >"$FIXTURE/spawn-victim"
  if [[ "$mode" == launcher ]]; then : >"$FIXTURE/finish-r11"; fi
  entry run >"$FIXTURE/launcher.log" 2>&1 & first=$!
  trap 'touch "$FIXTURE/release"; wait "$first" 2>/dev/null || true
        if [[ -n "${watchdog:-}" ]] && process_is_live "$watchdog" "$watchdog_start"; then
          kill -CONT "$watchdog" 2>/dev/null || true
        fi
        if [[ -n "${victim_start:-}" ]]; then
          c1_signal_owned TERM "$victim" "$session_path" "$victim_start" || true
        fi
        if [[ -n "${other:-}" ]]; then kill "$other" 2>/dev/null || true; wait "$other" 2>/dev/null || true; fi' EXIT
  wait_for_file "$FIXTURE/ready" || return 1
  read -r owner owner_start session_path <"$STATE/active" || return 1
  read -r watchdog watchdog_start <"$session_path/tmp/watchdog-ready" || return 1
  victim=$(cat "$FIXTURE/victim") || return 1
  victim_start=$(c1_proc_start "$victim") || return 1
  mkdir "$STATE/session.foreign" || return 1
  env C1_HARNESS_SESSION="$STATE/session.foreign" sleep 60 & other=$!
  other_start=$(c1_proc_start "$other") || return 1
  if [[ "$mode" == launcher ]]; then
    printf 'malformed record\n' >"$STATE/active"
  else
    printf '%s %s %s\n' "$other" "$other_start" "$STATE/session.foreign" >"$STATE/active"
  fi
  cp "$STATE/active" "$FIXTURE/preserved" || return 1
  if [[ "$mode" == watchdog ]]; then c1_record_privacy_failure "$session_path" teardown-failed || return 1; fi
  sleep 0.4
  process_is_live "$owner" "$owner_start" && process_is_live "$victim" "$victim_start" || return 1
  if [[ "$mode" == launcher ]]; then
    touch "$FIXTURE/release"
    wait "$first" || status=$?
    [[ "$status" == 1 ]] &&
      grep -qx -- '- Cleanup: FAIL' "$FIXTURE/r11.md" &&
      grep -qx -- '- R11: FAIL' "$FIXTURE/r11.md" || return 1
  else
    kill -STOP "$watchdog" 2>/dev/null || return 1
    kill -KILL "$owner" || return 1
    wait "$first" 2>/dev/null || true
    # The killed launcher's last short-lived child may still hold the lock.
    # Keep the watchdog stopped until the fixture actually owns it.
    for (( step=0; step<20; step++ )); do
      if c1_lock_state "$STATE"; then break; fi
      sleep 0.05
    done
    (( step < 20 )) || return 1
    kill -CONT "$watchdog" || return 1
    sleep 0.4
    process_is_live "$watchdog" "$watchdog_start" &&
      process_is_live "$victim" "$victim_start" && [[ -d "$session_path" ]] || return 1
    exec {C1_STATE_LOCK_FD}>&-
  fi
  wait_for_exit "$watchdog" "$watchdog_start" || return 1
  ! process_is_live "$victim" "$victim_start" &&
    [[ ! -e "$session_path" && -d "$STATE/session.foreign" ]] &&
    process_is_live "$other" "$other_start" &&
    cmp -s "$STATE/active" "$FIXTURE/preserved"
)

signal_identity_checks_start_time() (
  source "$BASE/lib/c1-lib.sh"
  local victim process_start
  python3 -I - "$FIXTURE" <<'PYTHON' &
import pathlib, signal, sys, time

root = pathlib.Path(sys.argv[1])
signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
(root / "signal-ready").touch()
for step in ("match", "mismatch"):
    while not (root / step).exists():
        time.sleep(0.01)
    if (signal.SIGUSR1 in signal.sigpending()) != (step == "match"):
        sys.exit(1)
    if step == "match":
        signal.sigwait({signal.SIGUSR1})
    (root / (step + "-checked")).touch()
PYTHON
  victim=$!
  trap 'kill "$victim" 2>/dev/null || true; wait "$victim" 2>/dev/null || true' EXIT
  wait_for_file "$FIXTURE/signal-ready" || return 1
  process_start=$(c1_proc_start "$victim") || return 1
  c1_signal_identity USR1 "$victim" "$process_start" || return 1
  touch "$FIXTURE/match"
  wait_for_file "$FIXTURE/match-checked" || return 1
  c1_signal_identity USR1 "$victim" "$((process_start+1))" || return 1
  touch "$FIXTURE/mismatch"
  wait_for_file "$FIXTURE/mismatch-checked" && wait "$victim"
)

run_finalization() (
  source "$BASE/lib/c1-lib.sh"
  eval "$(sed -n '/^clean_node() ($/,/^)/p; /^finalize_run() {$/,/^}$/p' "$BASE/c1-session.sh")"
  local HERE=$BASE ROOT=$STATE EVIDENCE_DIR variant expected_r11 expected_overall status digest
  local -a ev rp
  local PORT_PRIVATE=verified-probe-gh
  c1_port_free() { [[ "$variant" != cleanup-failure ]]; }
  c1_stop_privacy_monitor() { [[ "$variant" != missing-monitor-stop ]]; }
  c1_record_tree_descendants() {
    # Inject the privacy marker after the real finalizer has snapshotted meta.
    if [[ "$variant" == late-privacy ]]; then : >"$1/privacy-failed"; fi
  }
  c1_lock_state "$ROOT" || return 1
  for variant in clean late-privacy cleanup-failure missing-monitor-stop ambiguous-record; do
    S=$(c1_session_new "$ROOT" "$$") || return 1
    EVIDENCE_DIR="$FIXTURE/$variant"
    cat >"$S/meta" <<'META'
CHECKOUT_VERIFIED=PASS
HARNESS_CLEAN=true
CHECKS=harness_clean,auth_ancestor_and_approved_diff,production_review_status_recorded,exact_head_clean_worktree,vite_host_guard,webrtc_policy_pinned,localhost_loopback_only,ports_free,clean_after_dependency_install,listener_evidence_available
PLAYWRIGHT_VERSION=1.65.1
EXPECTED_PLAYWRIGHT_VERSION=1.65.1
BROWSER_VERSION=159.0.8154.6
EXPECTED_BROWSER_VERSION=159.0.8154.6
BROWSER_HEADED_VERSION=159.0.8154.6
EXPECTED_HEADED_BROWSER_VERSION=159.0.8154.6
PROD_REVIEW_PENDING=none
MODE=supervised
CODESPACES=true
PORT_PRIVATE=verified-probe-gh
LISTENER_EVIDENCE=verified
LISTENER_DETAIL=127.0.0.1:6080 pid=11 comm=websockify ino=31
META
    status=0 expected_r11=PASS expected_overall=INCOMPLETE
    if [[ "$variant" == late-privacy || "$variant" == ambiguous-record || "$variant" == missing-monitor-stop ]]; then expected_r11=FAIL; fi
    if [[ "$variant" == ambiguous-record ]]; then printf 'malformed record\n' >"$ROOT/active"; fi
    if [[ "$variant" != clean ]]; then expected_overall=FAIL; fi
    finalize_run 0 >"$FIXTURE/$variant.log" 2>&1 || status=$?
    if [[ "$variant" == clean ]]; then [[ "$status" == 0 ]] || return 1
    else [[ "$status" == 1 ]] || return 1; fi
    ev=("$EVIDENCE_DIR"/c1-evidence-*.md)
    rp=("$EVIDENCE_DIR"/c1-review-package-*.md)
    [[ ${#ev[@]} == 1 && ${#rp[@]} == 1 ]] || return 1
    grep -Eq "^\\| R11 \\| .* \\| \\*\\*$expected_r11\\*\\* \\|" "${ev[0]}" &&
      grep -Fq "**Harness summary: $expected_overall.**" "${ev[0]}" || return 1
    if [[ "$variant" == cleanup-failure || "$variant" == ambiguous-record ]]; then
      grep -Fq -- '- FAIL — inspect terminal diagnostics and retry cleanup' "${ev[0]}" || return 1
      grep -Fq -- 'Sensitive session files' "${ev[0]}" "${rp[0]}" || return 1
      if grep -Fq -- 'Verified cleanup deleted' "${ev[0]}" "${rp[0]}"; then return 1; fi
    else
      grep -Fq -- '- PASS — desktop and test processes stopped' "${ev[0]}" || return 1
      grep -Fq -- 'Verified cleanup deleted' "${ev[0]}" "${rp[0]}" || return 1
    fi
    digest=$(sha256sum "${ev[0]}") || return 1
    grep -Fxq "Evidence SHA-256 (of the section below, for transcription checks only): \`${digest%% *}\`" "${rp[0]}" || return 1
  done
)

monitor_shutdown_and_loss() (
  local variant launcher status session_path ws worker monitor register
  cat >"$FIXTURE/monitor-launcher.sh" <<'MONITOR_FIXTURE'
set -Eeuo pipefail
source "$1/lib/c1-lib.sh"
root=$2 fixture=$3 variant=$4
c1_port_free() { return 0; }  # No actual network listener in this fixture.
c1_lock_state "$root"
session_path=$(c1_session_new "$root" "$$")
export C1_HARNESS_SESSION=$session_path TMPDIR="$session_path/tmp"
trap 'c1_cleanup_launcher "$root" "$session_path"; c1_verify_cleanup "$session_path"' EXIT
trap '
  if [[ -n "${worker:-}" ]] && kill -0 "$worker" 2>/dev/null; then
    printf "desktop worker survived until launcher signal\n" >&2
    exit 2
  fi
  exit 130
' TERM
c1_start_watchdog "$root" "$session_path" "$$" "$1/lib/c1-lib.sh"
ws=$(c1_spawn "$session_path" websockify bash -c '
  setsid sleep 60 &
  printf "%s\n" "$!" >"$1"
  wait
' _ "$fixture/$variant.worker")
ws_start=$(c1_proc_start "$ws")
while [[ ! -s "$fixture/$variant.worker" ]]; do sleep 0.02; done
read -r worker <"$fixture/$variant.worker"
monitor=$(c1_spawn "$session_path" privacy-monitor bash -c '
  source "$1"
  c1_privacy_check() { return 0; }
  c1_port_free() { return 0; }
  C1_MONITOR_TRACK=1 c1_privacy_monitor_loop "$2" unused unused "$3" "$4" "" 0.05
' _ "$1/lib/c1-lib.sh" "$session_path" "$ws" "$ws_start")
monitor_start=$(c1_proc_start "$monitor")
while ! c1_monitor_healthy "$session_path" "$monitor" "$monitor_start"; do sleep 0.02; done
c1_arm_privacy_monitor "$session_path" "$monitor" "$monitor_start" "$ws" "$ws_start"
printf '%s %s %s\n' "$session_path" "$ws" "$monitor" >"$fixture/$variant.ready"
if [[ "$variant" == orderly ]]; then
  c1_stop_privacy_monitor "$session_path" "$monitor" "$monitor_start"
  if c1_owned_pid "$ws" "$session_path" "$ws_start" || kill -0 "$worker" 2>/dev/null; then
    exit 1
  fi
  ! c1_privacy_failed "$session_path"
else
  while :; do sleep 0.05; done
fi
MONITOR_FIXTURE

  for variant in orderly exited stalled; do
    timeout -k 4s 12s bash "$FIXTURE/monitor-launcher.sh" "$BASE" "$STATE" "$FIXTURE" "$variant" \
      >"$FIXTURE/$variant.log" 2>&1 &
    launcher=$!
    wait_for_file "$FIXTURE/$variant.ready" || return 1
    read -r session_path ws monitor <"$FIXTURE/$variant.ready" || return 1
    read -r worker <"$FIXTURE/$variant.worker" || return 1
    case "$variant" in
      exited) kill -TERM "$monitor" || return 1 ;;
      stalled) kill -STOP "$monitor" || return 1 ;;
    esac
    status=0
    wait "$launcher" || status=$?
    if [[ "$variant" == orderly ]]; then
      [[ "$status" == 0 ]] || return 1
    else
      [[ "$status" == 130 ]] || return 1
      register=$(c1_privacy_register "$session_path")
      grep -q ' monitor-unhealthy$' "$register" || return 1
    fi
    [[ ! -e "$session_path" ]] && ! kill -0 "$ws" 2>/dev/null && ! kill -0 "$worker" 2>/dev/null || return 1
  done
)

monitor_probe_failure_evidence() (
  local session="$STATE/session.probe" record
  local expected='authenticated probe transport failure (curl-exit-28 timing=0.010,0.020,0.030,0.000,0.400)'
  mkdir -m 700 "$session" || return 1
  c1_owned_pid() { return 0; }
  c1_privacy_check() {
    C1_PRIVACY_STATE=unverified
    C1_PRIVACY_DETAIL=$expected
    return 1
  }
  c1_teardown_novnc() {
    [[ "$5" == unverified && "$(c1_read_monitor_probe_failure "$1")" == "$expected" ]] ||
      return 1
    rmdir -- "$1"
  }
  c1_privacy_monitor_loop "$session" unused unused 11 31 '' 0 || return 1
  [[ ! -e "$session" && "$(c1_read_monitor_probe_failure "$session")" == "$expected" ]] || return 1
  c1_record_monitor_probe_failure "$session" 'authenticated probe did not reach the marker (HTTP 503)' ||
    return 1
  [[ "$(c1_read_monitor_probe_failure "$session")" == "$expected" ]] || return 1
  record=$(c1_monitor_probe_record "$session")
  printf '%s\n%s\n' 'bad arguments' 'unexpected report contents' >"$record"
  ! c1_read_monitor_probe_failure "$session"
)

harness_head_drift() (
  local repo="$FIXTURE/repo" pinned
  git init -q "$repo" &&
    mkdir "$repo/harness" &&
    printf 'same harness\n' >"$repo/harness/file" &&
    git -C "$repo" add harness &&
    git -C "$repo" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm initial || return 1
  pinned=$(git -C "$repo" rev-parse HEAD) || return 1
  c1_verify_harness_head "$repo" harness "$pinned" || return 1
  git -C "$repo" -c user.name=Fixture -c user.email=fixture@example.invalid commit --allow-empty -qm drift || return 1
  c1_verify_harness_clean "$repo" harness || return 1
  ! c1_verify_harness_head "$repo" harness "$pinned"
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
    'verification_is_retried:retry' 'launcher_ambiguous_record:launcher-record' \
    'watchdog_ambiguous_record:watchdog-record' 'watchdog_ambiguous_record:watchdog-lock' \
    'watchdog_exits_after_cleanup:watchdog-exit' \
    'run_finalization:privacy-flag' 'run_finalization:cleanup-verdict' 'run_finalization:post-hash-edit' \
    'signal_identity_checks_start_time:signal-identity'; do
    case_name=${spec%:*} mutation=${spec#*:}
    mutant=$(mktemp -d "$TMP/mutant.XXXXXXXX")
    mkdir "$mutant/lib"
    cp "$HERE/c1-session.sh" "$mutant/"
    cp "$HERE/lib/"{c1-lib.sh,c1-supervisor.py,c1-evidence.mjs} "$mutant/lib/"
    case $mutation in
      lock) sed -i '/^      c1_lock_state "\$ROOT"/d; /run|r11-only|preflight|cleanup|purge)/a\      :' "$mutant/c1-session.sh" ;;
      record) sed -i '/\[\[ "\$record" =~ \$pattern \]\]/i\  record=${record%%$'"'"'\\n'"'"'*}$'"'"'\\n'"'"'' "$mutant/lib/c1-lib.sh" ;;
      publication) sed -i 's/ln -T -- "$temp" "$root\/active"/mv -T -- "$temp" "$root\/active"/' "$mutant/lib/c1-lib.sh" ;;
      retry) sed -i 's/"$root"\/.session.\*.pending //' "$mutant/lib/c1-lib.sh" ;;
      privacy) sed -i 's/c1_record_privacy_failure "\$s" "\$reason" || evidence_error=1/:/' "$mutant/lib/c1-lib.sh" ;;
      pid)
        # A numeric-PID-only watchdog signals the unrelated reused PID.
        sed -i '/launcher_state=$(c1_owner_status "$owner" "$owner_start") || return 1/a\    if [[ "$launcher_state" == stale ]]; then kill -TERM "$owner"; return 0; fi' "$mutant/lib/c1-lib.sh"
        ;;
      r11)
        sed -i '/^    \[\[ "\$C1_FINAL_PRIVACY" == clean \]\] || PORT_PRIVATE=failed/d' "$mutant/c1-session.sh"
        ;;
      inherited-lock)
        sed -i 's/( c1_close_inherited_fds; exec "${pre\[@\]}"/( exec "${pre[@]}"/' "$mutant/lib/c1-lib.sh"
        ;;
      launcher-record) sed -i 's/|| ambiguous=1/|| return 1/' "$mutant/lib/c1-lib.sh" ;;
      watchdog-record) sed -i '/launcher_state=$(c1_owner_status/i\    c1_active_matches "$root" "$session" "$owner" "$owner_start" || return 0' "$mutant/lib/c1-lib.sh" ;;
      watchdog-lock) sed -i 's/ ]] \&\& c1_lock_state "$root"; then/ ]]; then/' "$mutant/lib/c1-lib.sh" ;;
      watchdog-exit) sed -i '/rm -f -- "$(c1_monitor_ack_path "\$session")"/a\      sleep 7' "$mutant/lib/c1-lib.sh" ;;
      privacy-flag) sed -i 's/ --privacy-failed="\$privacy_failed"//' "$mutant/c1-session.sh" ;;
      cleanup-verdict) sed -i '/if (args.cleanup === .FAIL.) result.overall/d' "$mutant/lib/c1-evidence.mjs" ;;
      post-hash-edit) sed -i 's/writeFileSync(ev, evidence,/writeFileSync(ev, evidence + "\\n",/' "$mutant/lib/c1-evidence.mjs" ;;
      signal-identity) sed -i '/if fields\[19\] == start.encode():/d; /signal.pidfd_send_signal(fd/s/^        /    /' "$mutant/lib/c1-lib.sh" ;;
      watchdog)
        sed -i '/c1_signal_identity TERM "\$owner"/i\      c1_cleanup_session "$root" "$session"' "$mutant/lib/c1-lib.sh"
        ;;
    esac
    if run_case "$mutant" "$case_name"; then
      printf 'FAIL: %s mutation escaped %s\n' "$mutation" "$case_name" >&2
      exit 1
    fi
    printf 'DETECTED: %s by %s\n' "$mutation" "$case_name"
  done
else
  for case_name in competing_launchers overlapping_cleaners malformed_active retained_privacy_failure reused_owner watchdog_waits_for_launcher r11_exit_status_and_late_privacy launcher_crash_recovery active_publication_preserves_owner verification_is_retried \
    launcher_ambiguous_record watchdog_ambiguous_record watchdog_exits_after_cleanup run_finalization signal_identity_checks_start_time monitor_shutdown_and_loss monitor_probe_failure_evidence harness_head_drift; do
    if ! run_case "$HERE" "$case_name"; then
      printf 'FAIL: %s\n' "$case_name" >&2
      for log in "$FIXTURE"/*.log; do [[ ! -f "$log" ]] || cat "$log" >&2; done
      exit 1
    fi
    printf 'PASS: %s\n' "$case_name"
  done
fi

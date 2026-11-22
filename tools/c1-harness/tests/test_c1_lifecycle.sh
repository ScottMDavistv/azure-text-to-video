#!/usr/bin/env bash
# shellcheck disable=SC2034  # Fixture variables are consumed by extracted entry points.
# Real launcher/cleaner processes with local-only fixtures and inert credentials.
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
  for variant in clean late startup-failed interrupted stop-failed cleanup-failed verify-failed; do
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
    if [[ "$variant" == startup-failed ]]; then
      PORT_PRIVATE=failed
      printf 'fixture unverified\n' >"$S/privacy-failed"
    fi
    status=0
    (
      trap 'finish_r11 "$?"' EXIT
      if [[ "$variant" == startup-failed ]]; then exit 1; fi
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
      startup-failed|interrupted)
        local expected_status=1
        if [[ "$variant" == interrupted ]]; then expected_status=130; fi
        [[ "$status" == "$expected_status" ]] &&
          grep -Fxq -- '- Session cleanup: PASS' "$R11_EVIDENCE" &&
          grep -Fxq -- '- Post-cleanup verification: PASS' "$R11_EVIDENCE" &&
          grep -Fxq -- '- R11: FAIL' "$R11_EVIDENCE" || return 1
        if [[ "$variant" == startup-failed ]]; then
          grep -Fxq -- '- Monitor shutdown: not-run' "$R11_EVIDENCE" &&
            grep -Fxq -- '- Final privacy evidence: failed' "$R11_EVIDENCE" &&
            [[ -s "$(c1_privacy_register "$S")" ]] || return 1
        fi
        [[ ! -e "$ROOT/.${S##*/}.pending" && ! -e "$ROOT/active" ]] || return 1
        ;;
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
  local variant launcher status session_path ws worker monitor register started closed
  local owner supervisor supervisor_start connection step verified
  cat >"$FIXTURE/monitor-launcher.sh" <<'MONITOR_FIXTURE'
set -Eeuo pipefail
source "$1/lib/c1-lib.sh"
root=$2 fixture=$3 variant=$4

c1_lock_state "$root"
session_path=$(c1_session_new "$root" "$$")
export C1_HARNESS_SESSION=$session_path TMPDIR="$session_path/tmp"
trap 'c1_cleanup_launcher "$root" "$session_path"; c1_verify_cleanup "$session_path"' EXIT
trap '
  if [[ -n "${worker:-}" ]] && kill -0 "$worker" 2>/dev/null; then
    printf "desktop worker survived until launcher signal\n" >&2
    exit 2
  fi
  if ! c1_port_free "$C1_NOVNC_PORT" ||
     ! grep -Fxq "$supervisor $supervisor_start" "$(c1_reaped_register "$session_path")"; then
    exit 3
  fi
  c1_monitor_tick >"$fixture/$variant.closed"
  exit 130
' TERM
c1_start_watchdog "$root" "$session_path" "$$" "$1/lib/c1-lib.sh"
cat >"$fixture/worker.py" <<'PYTHON'
import os
import signal
import socket
import sys
from pathlib import Path

with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # The exposure bound starts before bind, not before the supervisor's
    # non-listening acknowledgment wait. Use the same kernel monotonic clock.
    tick = Path("/proc/uptime").read_text().split()[0].replace(".", "")
    Path(sys.argv[1] + ".bound").write_text(tick + "\n", encoding="ascii")
    listener.bind(("127.0.0.1", int(sys.argv[2])))
    listener.listen()
    child = os.fork()
    if child:
        listener.close()
        os.waitpid(child, 0)
    else:
        os.setsid()
        ready = Path(sys.argv[1])
        if ready.name == "launcher-loss.worker":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            ready.write_text(f"{os.getpid()}\n", encoding="ascii")
            with listener.accept()[0] as connection:
                connection.sendall(b"connected\n")
                signal.pause()
        else:
            ready.write_text(f"{os.getpid()}\n", encoding="ascii")
            signal.pause()
PYTHON
ws=$(C1_GUARD_STARTUP=1 c1_spawn_desktop "$session_path" websockify python3 -I "$fixture/worker.py" \
  "$fixture/$variant.worker" "$C1_NOVNC_PORT")
read -r _ _ supervisor supervisor_start <"$session_path/websockify-supervisor"
ws_start=$(c1_proc_start "$ws")
while [[ ! -s "$fixture/$variant.worker" ]]; do sleep 0.02; done
read -r worker <"$fixture/$variant.worker"
if [[ "$variant" == startup ]]; then
  read -r started <"$fixture/$variant.worker.bound"
  printf '%s\n' "$started" >"$fixture/$variant.started"
  printf '%s %s %s\n' "$session_path" "$ws" 0 >"$fixture/$variant.ready"
  while :; do sleep 0.05; done
fi
monitor=$(c1_spawn "$session_path" privacy-monitor bash -c '
  source "$1"
  fail_probe=$5
  c1_privacy_check() {
    if [[ -f "$fail_probe" ]]; then
      C1_PRIVACY_STATE=unverified
      C1_PRIVACY_DETAIL="authenticated probe transport failure (curl-exit-28)"
      return 1
    fi
    return 0
  }
  C1_MONITOR_TRACK=1 c1_privacy_monitor_loop "$2" unused unused "$3" "$4" "" 0.05
' _ "$1/lib/c1-lib.sh" "$session_path" "$ws" "$ws_start" "$fixture/$variant.fail-probe")
monitor_start=$(c1_proc_start "$monitor")
while ! c1_monitor_healthy "$session_path" "$monitor" "$monitor_start"; do sleep 0.02; done
c1_arm_privacy_monitor "$session_path" "$monitor" "$monitor_start" "$ws" "$ws_start"
if [[ "$variant" == launcher-loss ]]; then
  # Let the test hold the lifecycle lock while the watchdog drains the tree.
  exec {C1_STATE_LOCK_FD}>&-
  printf '%s %s %s\n' "$$" "$supervisor" "$supervisor_start" >"$fixture/$variant.owner"
fi
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

  for variant in startup orderly exited stalled ambiguous launcher-loss; do
    # This guard covers startup, heartbeat expiry and full process cleanup.
    timeout -k 4s 30s bash "$FIXTURE/monitor-launcher.sh" "$BASE" "$STATE" "$FIXTURE" "$variant" \
      >"$FIXTURE/$variant.log" 2>&1 &
    launcher=$!
    wait_for_file "$FIXTURE/$variant.ready" || return 1
    read -r session_path ws monitor <"$FIXTURE/$variant.ready" || return 1
    read -r worker <"$FIXTURE/$variant.worker" || return 1
    case "$variant" in
      exited) kill -TERM "$monitor" || return 1 ;;
      stalled|ambiguous)
        started=$(c1_monitor_tick) || return 1
        if [[ "$variant" == stalled ]]; then
          kill -STOP "$monitor" || return 1
        else
          : >"$FIXTURE/$variant.fail-probe"
        fi
        ;;
      launcher-loss)
        read -r owner supervisor supervisor_start <"$FIXTURE/$variant.owner" || return 1
        exec {connection}<>"/dev/tcp/127.0.0.1/$C1_NOVNC_PORT" || return 1
        IFS= read -r -t 2 -u "$connection" closed && [[ "$closed" == connected ]] || return 1
        c1_lock_state "$STATE" || return 1
        register=$(c1_reaped_register "$session_path") || return 1
        started=$(c1_monitor_tick) || return 1
        kill -KILL "$owner" || return 1
        verified=0
        for ((step=0; step<125; step++)); do
          if grep -Fxsq -- "$supervisor $supervisor_start" "$register" &&
             c1_port_free "$C1_NOVNC_PORT" && ! kill -0 "$worker" 2>/dev/null; then
            verified=1
            break
          fi
          sleep 0.02
        done
        closed=$(c1_monitor_tick) || return 1
        exec {connection}>&-
        exec {C1_STATE_LOCK_FD}>&-
        wait "$launcher" || true
        for ((step=0; step<150; step++)); do
          if [[ ! -e "$session_path" && ! -e "$STATE/active" ]] &&
             ! kill -0 "$owner" 2>/dev/null && ! kill -0 "$worker" 2>/dev/null; then break; fi
          sleep 0.02
        done
        (( verified && 10#$closed >= 10#$started && 10#$closed - 10#$started <= 250 )) || return 1
        [[ ! -e "$session_path" && ! -e "$STATE/active" ]] &&
          ! kill -0 "$owner" 2>/dev/null && ! kill -0 "$worker" 2>/dev/null || return 1
        printf 'Launcher loss to verified worker reaping and port closure: %d ms\n' "$(( (10#$closed - 10#$started) * 10 ))"
        continue
        ;;
    esac
    status=0
    wait "$launcher" || status=$?
    if [[ "$variant" == orderly ]]; then
      [[ "$status" == 0 ]] || return 1
    else
      [[ "$status" == 130 ]] || return 1
      register=$(c1_privacy_register "$session_path")
      if [[ "$variant" == startup ]]; then
        grep -q ' startup-timeout$' "$register" || return 1
        read -r started <"$FIXTURE/$variant.started" || return 1
      else
        grep -q ' monitor-unhealthy$' "$register" || return 1
        if [[ "$variant" == ambiguous ]]; then grep -q ' unverified$' "$register" || return 1; fi
      fi
      if [[ "$variant" != exited ]]; then
        read -r closed <"$FIXTURE/$variant.closed" || return 1
        (( 10#$closed >= 10#$started && 10#$closed - 10#$started <= 250 )) || return 1
        printf '%s to verified reaping and port closure: %d ms\n' "$variant" "$(( (10#$closed - 10#$started) * 10 ))"
      fi
    fi
    [[ ! -e "$session_path" ]] && ! kill -0 "$ws" 2>/dev/null && ! kill -0 "$worker" 2>/dev/null || return 1
  done
)

websockify_reaping_ack() (
  source "$BASE/lib/c1-lib.sh"
  local session="$STATE/session.fixture" reaped signal_count=0
  mkdir "$session" || return 1
  printf '11 31 12 32\n' >"$session/websockify-supervisor"
  printf '12 32\n' >"$session/supervisors"
  reaped=$(c1_reaped_register "$session") || return 1

  c1_port_free() { return 0; }  # Give every attempt positive port closure evidence.
  c1_signal_owned() {
    [[ "$*" == "USR1 12 $session 32" ]] || return 1
    signal_count=$((signal_count + 1))
  }
  sleep() { :; }
  if c1_close_websockify_tree "$session" 6080 11 31 1; then return 1; fi
  printf '12 33\n13 32\n' >"$reaped"
  if c1_close_websockify_tree "$session" 6080 11 31 1; then return 1; fi
  printf '12 32\n' >>"$reaped"
  c1_close_websockify_tree "$session" 6080 11 31 1 && [[ "$signal_count" == 3 ]]
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
  source "$BASE/lib/c1-lib.sh"
  eval "$(sed -n '/^clean_node() ($/,/^)/p; /^on_exit() {$/,/^}$/p; /^skip_phase() /p; /^session() {$/,/^}$/p; /^finalize_run() {$/,/^}$/p; /^main() {$/,/^}$/p' "$BASE/c1-session.sh")"
  local HERE=$BASE ROOT=$STATE REPO="$FIXTURE/repo" HARNESS_REL=harness pinned variant status
  local WT="$FIXTURE/approved" FE EVIDENCE_DIR S='' AUTH PW LIB="$BASE/lib/c1-lib.sh"
  local PROD_PENDING=none
  local -a git_fixture=(git -C "$REPO" -c user.name=Fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false)
  local -a CHECKS=(harness_clean auth_ancestor_and_approved_diff production_review_status_recorded
    exact_head_clean_worktree vite_host_guard webrtc_policy_pinned localhost_loopback_only ports_free
    clean_after_dependency_install listener_evidence_available)
  mkdir "$REPO" &&
    "${git_fixture[@]}" init -q &&
    mkdir "$REPO/harness" "$REPO/frontend" &&
    mkdir "$REPO/frontend/e2e" &&
    touch "$REPO/frontend/e2e/run-browser-mutations.mjs" &&
    printf 'same harness\n' >"$REPO/harness/file" &&
    "${git_fixture[@]}" add harness frontend &&
    "${git_fixture[@]}" commit -qm initial || return 1
  pinned=$("${git_fixture[@]}" rev-parse HEAD) || return 1
  AUTH=$pinned PW=$pinned

  scrub_env() { :; }
  preflight() { c1_verify_harness_clean "$REPO" "$HARNESS_REL"; }
  install_deps() { c1_verify_clean_tree "$WT" "$PW"; }
  c1_port_free() { return 0; }
  c1_expected_browser() { printf '%s\n' "$C1_CHROMIUM_VERSION"; }
  node() {
    if [[ "$1" == -p ]]; then printf '%s\n' "$C1_PLAYWRIGHT_VERSION"
    else command node "$@"; fi
  }
  eval "$(declare -f clean_node | sed '1s/clean_node/fixture_clean_node/')"
  clean_node() {
    if [[ "$1" == --input-type=module ]]; then printf '%s\n' "$C1_CHROMIUM_VERSION"
    else fixture_clean_node "$@"; fi
  }
  run_phase() {
    if [[ "$variant" == drift && "$1" == unconfigured ]]; then
      "${git_fixture[@]}" commit --allow-empty -qm drift &&
        c1_verify_harness_clean "$REPO" "$HARNESS_REL" || exit 2
    fi
    if [[ "$1" == static-mutation || "$1" == browser-mutation ]]; then
      local now
      now=$(c1_utc) || exit 2
      printf '%s|ran|%s|%s|0\n' "$1" "$now" "$now" >>"$S/phases"
    else
      skip_phase "$1"
    fi
  }

  # Use a separate approved checkout to isolate harness drift from worktree drift.
  git clone -q --local "$REPO" "$WT" &&
    git -C "$WT" checkout -q --detach "$PW" || return 1
  FE="$WT/frontend"
  for variant in clean drift; do
    EVIDENCE_DIR="$FIXTURE/$variant"
    status=0
    ( main run --offline-only ) >"$FIXTURE/$variant.log" 2>&1 || status=$?
    local evidence=("$EVIDENCE_DIR"/c1-evidence-*.md)
    local review=("$EVIDENCE_DIR"/c1-review-package-*.md)
    grep -Fq -- "Harness: \`tools/c1-harness/\` at \`$pinned\`" "${review[0]}" &&
      grep -Fq -- '- PASS — desktop and test processes stopped' "${evidence[0]}" || return 1
    if [[ "$variant" == clean ]]; then
      [[ "$status" == 0 ]] &&
        grep -Fq "**Harness summary: INCOMPLETE.**" "${evidence[0]}" &&
        grep -Fq "| Harness SHA | \`$pinned\` (clean: true) |" "${evidence[0]}" || return 1
    else
      [[ "$status" == 1 && "$("${git_fixture[@]}" rev-parse HEAD)" != "$pinned" ]] &&
        grep -Fq "**Harness summary: FAIL.**" "${evidence[0]}" &&
        grep -Fq '| R1 | Clean exact-head checkout verification | **FAIL** |' "${evidence[0]}" &&
        grep -Fq "| Harness SHA | \`$pinned\` (clean: false) |" "${evidence[0]}" || return 1
    fi
  done
)


# No actual desktop or external forwarding: the normal launcher function,
# startup guard, supervisor, listener checks, monitor and cleanup all run.
# Only unavailable display/forwarding boundaries are inert local fixtures.
guarded_delayed_startup() (
  local variant status
  cp "$BASE/lib/"{c1-lib.sh,c1-supervisor.py} "$FIXTURE/"
  cat >>"$FIXTURE/c1-lib.sh" <<'BOUNDARIES'
c1_gh_visibility() { printf 'private\n'; }
c1_https_probe() {
  local url=$1 output=$2 hdr=${3:-} s=${TMPDIR%/tmp}
  : >"$output"
  if [[ -z "$hdr" ]]; then
    sleep 0.1  # bounded local forwarding latency
    printf 'anonymous\n' >>"$s/probes"
    if [[ -e "$s/deny-preflight" ]]; then printf '503\n'; else printf '403\n'; fi
  else
    sleep 0.3
    printf 'authenticated\n' >>"$s/probes"
    if [[ -e "$s/deny-monitor" && "$url" == */vnc.html ]]; then printf '503\n'
    else printf '%s\n' "$C1_NOVNC_SIGNATURE" >"$output"; printf '200\n'; fi
  fi
}
c1_start_display_vnc() { c1_port_free "$C1_NOVNC_PORT"; }
c1_verify_private_with_decoy() {
  c1_port_free "$2" &&
    c1_privacy_check "$3" /preflight "$C1_NOVNC_SIGNATURE" "$4" &&
    touch "$1/prechecked"
}
c1_cmd_websockify() {
  printf '%s\n' python3 -I "${TMPDIR%/tmp}/worker.py" "$2" "$1"
}
BOUNDARIES
  cat >"$FIXTURE/start.sh" <<'START'
set -Eeuo pipefail
harness=$1 fixture=$2 variant=$3
source "$fixture/c1-lib.sh"
eval "$(sed -n '/^start_private_desktop() {$/,/^}$/p; /^fail_desktop() {$/,/^}$/p' "$harness/c1-session.sh")"
root="$fixture/state-$variant"
c1_lock_state "$root"
S=$(c1_session_new "$root" "$$")
export C1_HARNESS_SESSION=$S TMPDIR="$S/tmp"
export CODESPACE_NAME=c1-offline-fixture GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN=app.github.dev
export GITHUB_TOKEN=INERT_LOCAL_FIXTURE_TOKEN UNEXPECTED_DESKTOP_SECRET=must-not-inherit
LIB="$fixture/c1-lib.sh" PORT_PRIVATE=unverified WS='' WSST='' MONITOR_PID='' MONITOR_START=''
finish() {
  local rc=$?
  trap - EXIT
  trap '' INT TERM HUP  # Match the launcher's non-interruptible cleanup trap.
  cp "$S/probes" "$fixture/$variant.probes" 2>/dev/null || true
  [[ ! -e "$S/worker-started" ]] || cp "$S/worker-started" "$fixture/$variant.worker"
  c1_cleanup_launcher "$root" "$S" || rc=90
  c1_verify_cleanup "$S" || rc=91
  if [[ -f "$(c1_privacy_register "$S")" ]]; then
    cp "$(c1_privacy_register "$S")" "$fixture/$variant.privacy.log"
  fi
  [[ ! -e "$S" && ! -e "$root/active" ]] && c1_port_free "$C1_NOVNC_PORT" || rc=92
  printf '%s\n' "$S" >"$fixture/$variant.cleaned"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' TERM
[[ "$variant" != deny-preflight ]] || touch "$S/deny-preflight"
[[ "$variant" != deny-monitor ]] || touch "$S/deny-monitor"
[[ "$variant" != never-listens ]] || touch "$S/never-listens"
cp "$fixture/worker.py" "$S/worker.py"
c1_start_watchdog "$root" "$S" "$$" "$LIB"
if start_private_desktop INERT_PASSWORD; then
  [[ "$variant" == success && "$PORT_PRIVATE" == verified-probe-gh ]] || exit 10
  [[ -s "$S/worker-started" && -f "$S/startup-watched" && -f "$S/monitor-watched" ]] || exit 11
  grep -qx anonymous "$S/probes" && grep -qx authenticated "$S/probes" || exit 12
  c1_monitor_healthy "$S" "$MONITOR_PID" "$MONITOR_START" || exit 13
  c1_stop_privacy_monitor "$S" "$MONITOR_PID" "$MONITOR_START" || exit 14
  c1_port_free "$C1_NOVNC_PORT" && ! c1_privacy_failed "$S" || exit 15
else
  [[ "$variant" != success ]] || exit 16
  if [[ "$variant" == deny-preflight ]]; then
    [[ ! -e "$S/startup" && ! -e "$S/worker-started" ]] || exit 17
  else
    c1_privacy_failed "$S" && c1_port_free "$C1_NOVNC_PORT" || exit 18
  fi
fi
START
  cat >"$FIXTURE/worker.py" <<'WORKER'
import os
import signal
import socket
import sys
import time
from pathlib import Path

session = Path(sys.argv[1])
# Simulate a cold Python/websockify import before binding. No exposure exists.
time.sleep(8.0 if (session / "never-listens").exists() else 2.0)
assert (session / "prechecked").is_file()
assert (session / "startup-watched").is_file()
assert os.environ.get("C1_GUARD_STARTUP") == "1"
assert "GITHUB_TOKEN" not in os.environ
assert "UNEXPECTED_DESKTOP_SECRET" not in os.environ
assert any(line.split()[0] == str(os.getppid())
           for line in (session / "supervisors").read_text().splitlines())
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(sys.argv[2])))
    listener.listen()
    (session / "worker-started").write_text(f"{os.getpid()}\n")
    signal.pause()
WORKER
  for variant in success deny-preflight deny-monitor never-listens; do
    status=0
    timeout -k 4s 25s bash "$FIXTURE/start.sh" "$BASE" "$FIXTURE" "$variant" \
      >"$FIXTURE/$variant.log" 2>&1 || status=$?
    [[ -s "$FIXTURE/$variant.cleaned" ]] || { printf '%s: missing cleanup, exit %s\n' "$variant" "$status" >&2; return 1; }
    if [[ "$variant" == never-listens ]]; then
      [[ "$status" == 130 && ! -e "$FIXTURE/$variant.worker" ]] || return 1
      local cleaned
      read -r cleaned <"$FIXTURE/$variant.cleaned" || return 1
      grep -q ' startup-timeout$' "$(c1_privacy_register "$cleaned")" || return 1
      continue
    fi
    [[ "$status" == 0 ]] || { printf '%s: exit %s\n' "$variant" "$status" >&2; return 1; }
    if [[ "$variant" == deny-preflight ]]; then
      [[ ! -e "$FIXTURE/$variant.worker" ]] || return 1
    else
      local worker
      read -r worker <"$FIXTURE/$variant.worker" || return 1
      ! kill -0 "$worker" 2>/dev/null || return 1
    fi
  done
)

# A verified stop can complete between the watchdog's acknowledgment and
# health reads. Only the exact monitor acknowledgment may suppress teardown.
monitor_stop_ack_race() (
  source "$BASE/lib/c1-lib.sh"
  local session="$STATE/session.ack" checks=0
  mkdir "$session" || return 1
  printf '11 31 12 32\n' >"$session/monitor-required"
  c1_owner_status() { printf 'active\n'; }
  c1_monitor_stopped() { checks=$((checks + 1)); (( checks > 1 )); }
  c1_monitor_healthy() { return 1; }
  c1_teardown_novnc() { touch "$FIXTURE/unexpected-teardown"; }
  c1_active_matches() { return 1; }
  c1_marked_pids() { :; }
  sleep() { rm -rf -- "$session"; }
  c1_watchdog_loop "$STATE" "$session" 13 33 || return 1
  [[ "$checks" == 2 && ! -e "$FIXTURE/unexpected-teardown" ]]
)

drain_timeout_evidence() (
  source "$BASE/lib/c1-lib.sh"
  local session="$STATE/session.timeout" register C1_CLOSE_DEADLINE=150
  mkdir "$session" || return 1
  printf '11 31 12 32\n' >"$session/websockify-supervisor"
  printf '12 32\n' >"$session/supervisors"
  # Inert identities, no process, no listener. Clock is already past the
  # caller's drain deadline; termination must still be requested, but late
  # reaping and positive port closure must never turn timeout into success.
  c1_monitor_tick() { printf '151\n'; }
  c1_signal_owned() { [[ "$*" == "USR1 12 $session 32" ]]; }
  c1_port_free() { return 0; }
  printf '12 32\n' >"$(c1_reaped_register "$session")"
  if c1_teardown_novnc "$session" 6080 11 31 monitor-unhealthy; then return 1; fi
  [[ "$C1_CLOSE_DEADLINE" == 150 ]] || return 1
  register=$(c1_privacy_register "$session") || return 1
  grep -q ' teardown-failed$' "$register" || return 1
  # Remove only inert identity fixtures before exercising real cleanup.
  rm -- "$session/supervisors" "$session/websockify-supervisor" "$(c1_reaped_register "$session")"
  c1_cleanup_session "$STATE" "$session" && c1_verify_cleanup "$session" || return 1
  [[ ! -e "$session" && -s "$register" ]] &&
    grep -q ' teardown-failed$' "$register" && c1_privacy_failed "$session"
)

run_case() {
  BASE=$1 FIXTURE=$(mktemp -d "$TMP/case.XXXXXXXX")
  STATE="$FIXTURE/state"
  mkdir -m 700 "$STATE"
  "$2"
}

# Negative lifecycle cases can return while their watchdog completes cleanup.
# This is test isolation AFTER timing assertions, never extra drain time for
# the product. Do not let a busy port make the next mutant fail spuriously.
wait_for_fixture_port_release() {
  local now deadline
  now=$(c1_monitor_tick) || return 1
  deadline=$((10#$now + 1000))
  while c1_deadline_remaining "$deadline" >/dev/null; do
    c1_port_free "$C1_NOVNC_PORT" && return 0
    sleep 0.05
  done
  printf 'FAIL: prior fixture still owns the test port\n' >&2
  return 1
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
    'signal_identity_checks_start_time:signal-identity' \
    'r11_exit_status_and_late_privacy:trap-status' 'r11_exit_status_and_late_privacy:monitor-stop' \
    'websockify_reaping_ack:port-only' 'harness_head_drift:harness-drift' \
    'monitor_shutdown_and_loss:launcher-loss-drain' \
    'guarded_delayed_startup:startup-guard' 'guarded_delayed_startup:cold-start-budget' \
    'drain_timeout_evidence:drain-deadline' 'monitor_stop_ack_race:stop-ack-race'; do
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
      trap-status) sed -i '/rm -f -- "\$root\/active"$/{n;s/return "\$?"/return/;}' "$mutant/lib/c1-lib.sh" ;;
      monitor-stop) sed -i '/monitor_shutdown="FAIL (exit \$?)"/{n;/PORT_PRIVATE=failed/d;}' "$mutant/c1-session.sh" ;;
      stop-ack-race) sed -i '/! c1_monitor_stopped .*; then/s/! c1_monitor_stopped .*; then/true; then/' "$mutant/lib/c1-lib.sh" ;;
      cold-start-budget) sed -i 's/startup_tick + 500/startup_tick + 150/' "$mutant/lib/c1-lib.sh" ;;
      startup-guard) sed -i 's/WS=$(C1_GUARD_STARTUP=1 /WS=$(C1_GUARD_STARTUP=0 /' "$mutant/c1-session.sh" ;;
      drain-deadline) sed -i '/c1_deadline_remaining "\$C1_CLOSE_DEADLINE" >\/dev\/null || return 1/d; s/ \&\& c1_deadline_remaining "\$C1_CLOSE_DEADLINE" >\/dev\/null; then/; then/' "$mutant/lib/c1-lib.sh" ;;
      port-only) sed -i '/^    if .*"\$reg".*grep -Fxq/c\    if' "$mutant/lib/c1-lib.sh" ;;
      harness-drift) sed -i '/^  c1_verify_harness_head .*HARNESS_CLEAN=false/d' "$mutant/c1-session.sh" ;;
      launcher-loss-drain) sed -i '/if \[\[ "\$launcher_state" == stale \&\& -n "\$startup_pid" \]\]; then/,/^    fi$/d' "$mutant/lib/c1-lib.sh" ;;
      watchdog)
        sed -i '/c1_signal_identity TERM "\$owner"/i\      c1_cleanup_session "$root" "$session"' "$mutant/lib/c1-lib.sh"
        ;;
    esac
    if cmp -s "$HERE/lib/c1-lib.sh" "$mutant/lib/c1-lib.sh" &&
       cmp -s "$HERE/c1-session.sh" "$mutant/c1-session.sh" &&
       cmp -s "$HERE/lib/c1-evidence.mjs" "$mutant/lib/c1-evidence.mjs"; then
      printf 'FAIL: %s transformation changed no fixture\n' "$mutation" >&2
      exit 1
    fi
    bash -n "$mutant/lib/c1-lib.sh" "$mutant/c1-session.sh" || exit 1
    wait_for_fixture_port_release || exit 1
    if ! run_case "$HERE" "$case_name"; then
      printf 'FAIL: unmodified baseline for %s (%s)\n' "$mutation" "$case_name" >&2
      exit 1
    fi
    wait_for_fixture_port_release || exit 1
    if run_case "$mutant" "$case_name"; then
      printf 'FAIL: %s mutation escaped %s\n' "$mutation" "$case_name" >&2
      exit 1
    fi
    printf 'DETECTED: %s by %s (baseline passed)\n' "$mutation" "$case_name"
    wait_for_fixture_port_release || exit 1
  done
else
  for case_name in competing_launchers overlapping_cleaners malformed_active retained_privacy_failure reused_owner watchdog_waits_for_launcher r11_exit_status_and_late_privacy launcher_crash_recovery active_publication_preserves_owner verification_is_retried \
    launcher_ambiguous_record watchdog_ambiguous_record watchdog_exits_after_cleanup run_finalization signal_identity_checks_start_time monitor_shutdown_and_loss websockify_reaping_ack monitor_probe_failure_evidence harness_head_drift guarded_delayed_startup drain_timeout_evidence monitor_stop_ack_race; do
    if ! run_case "$HERE" "$case_name"; then
      printf 'FAIL: %s\n' "$case_name" >&2
      for log in "$FIXTURE"/*.log; do [[ ! -f "$log" ]] || cat "$log" >&2; done
      exit 1
    fi
    printf 'PASS: %s\n' "$case_name"
  done
fi

#!/usr/bin/env bash
# C1 Microsoft Entra validation harness — ONE COMMAND, development only.
#
#   bash tools/c1-harness/c1-session.sh              # full supervised session
#   bash tools/c1-harness/c1-session.sh --offline-only
#   bash tools/c1-harness/c1-session.sh preflight | cleanup | purge | selftest
#
# Runs the approved PR #60 Playwright suite UNMODIFIED from a detached, clean
# worktree at the pinned approved SHA, inside a private Xvfb/noVNC desktop
# reachable only through Codespaces private port forwarding. It never stores
# Microsoft credentials, tokens, cookies or browser artifacts, never deploys,
# never touches Azure, and never certifies C1 by itself.
set -Eeuo pipefail
umask 077

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/c1-lib.sh
source "$HERE/lib/c1-lib.sh"

REPO=$(git -C "$HERE" rev-parse --show-toplevel)
HARNESS_REL=$(realpath --relative-to="$REPO" "$HERE")
AUTH=$C1_AUTH_SHA_APPROVED
PW=$C1_PW_SHA_APPROVED
CACHE=${C1_CACHE_ROOT:-$HOME/.cache/c1-harness}
EVIDENCE_DIR=${C1_EVIDENCE_DIR:-$HOME/c1-evidence}
WT="$CACHE/approved-${PW:0:12}"
FE="$WT/frontend"
ROOT=$(c1_state_root)
S=''
CHECKS=()

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# Variables that could change test semantics or leak data are never inherited.
scrub_env() {
  unset C1_LIVE C1_UNCONFIGURED C1_MUTATION_MODE C1_EXPECTED_SHA C1_SUPERVISED_CASE \
    VITE_ENTRA_SPA_CLIENT_ID VITE_C1_SHA VITE_C1_DIRTY PWDEBUG DEBUG \
    PLAYWRIGHT_JSON_OUTPUT_NAME PLAYWRIGHT_JSON_OUTPUT_FILE PWTEST_SKIP_TEST_OUTPUT \
    PLAYWRIGHT_HTML_REPORT NODE_OPTIONS CI 2>/dev/null || true
}

check() { CHECKS+=("$1"); }

preflight() {
  local t major
  for t in git node npm sha256sum setsid realpath awk getent mktemp; do
    command -v "$t" >/dev/null || { c1_err "required tool missing: $t"; return 1; }
  done
  major=$(node -p 'process.versions.node.split(".")[0]')
  (( major >= 20 )) || { c1_err "Node.js >= 20 required (found $(node --version))"; return 1; }
  c1_verify_harness_clean "$REPO" "$HARNESS_REL" && check harness_clean
  c1_fetch_commit "$REPO" "$AUTH" && c1_fetch_commit "$REPO" "$PW" || { c1_err 'cannot obtain approved commits'; return 1; }
  c1_verify_commits "$REPO" "$AUTH" "$PW" "$C1_AUTH_TO_PW_FILES" && check auth_ancestor_and_approved_diff
  c1_prepare_worktree "$REPO" "$PW" "$WT" && check exact_head_clean_worktree
  c1_check_localhost && check localhost_loopback_only
  c1_port_free "$C1_APP_PORT" || { c1_err "port $C1_APP_PORT in use; stop the existing dev server (fresh server is mandatory)"; return 1; }
  c1_port_free "$C1_NOVNC_PORT" || { c1_err "port $C1_NOVNC_PORT in use; run: c1-session.sh cleanup"; return 1; }
  check ports_free
}

install_deps() {
  mkdir -p "$CACHE"
  c1_install_apt_packages
  command -v ss >/dev/null || { c1_err 'ss (iproute2) is required for listener verification'; return 1; }
  c1_install_node_deps "$FE" "$CACHE"
  c1_verify_clean_tree "$WT" "$PW" && check clean_after_dependency_install
}

on_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  if [[ -n "$S" && -d "$S" ]]; then
    c1_log 'cleaning up: stopping desktop/test processes, closing listeners, deleting session files'
    c1_cleanup_session "$ROOT" "$S" || true
  fi
  exit "$rc"
}

pw_env() {
  env -u C1_LIVE -u C1_UNCONFIGURED -u C1_MUTATION_MODE \
    TMPDIR="$S/tmp" PLAYWRIGHT_JSON_OUTPUT_NAME="$S/reports/$1.json" "${@:2}"
}

# run_phase ID DIR CMD...   (never aborts the session on test failure)
run_phase() {
  local id=$1 dir=$2 start rc; shift 2
  start=$(c1_utc)
  c1_log "phase $id: starting ($start)"
  set +e
  (cd "$dir" && "$@")
  rc=$?
  set -e
  printf '%s|ran|%s|%s|%s\n' "$id" "$start" "$(c1_utc)" "$rc" >>"$S/phases"
  c1_log "phase $id: finished with exit $rc"
}

skip_phase() { printf '%s|skipped|-|-|-\n' "$1" >>"$S/phases"; c1_log "phase $1: not executed (INCOMPLETE)"; }

ask_phase() {
  local id=$1 answer
  printf '\n==== %s ====\n%s\n' "$2" "$3" >&2
  read -r -p "Press Enter to start, or type s to skip (row becomes INCOMPLETE): " answer
  [[ "$answer" != s && "$answer" != S ]] || { skip_phase "$id"; return 1; }
}

stage_supplemental() {
  mkdir -p "$S/supp"
  cp "$HERE/supplemental/playwright.supplemental.config.mjs" "$HERE/supplemental/c1-supervised.spec.mjs" "$S/supp/"
  ln -s "$FE/node_modules" "$S/supp/node_modules"
}

run_supplemental() {
  local case=$1
  run_phase "$case" "$S/supp" pw_env "$case" C1_LIVE=1 C1_EXPECTED_SHA="$PW" C1_SUPERVISED_CASE="$case" \
    C1_WORKTREE_FRONTEND="$FE" VITE_ENTRA_SPA_CLIENT_ID="$CLIENT_ID" \
    npx --no-install playwright test --config="$S/supp/playwright.supplemental.config.mjs" \
    --headed --workers=1 --reporter=list,json
}

print_access() {
  local pass=$1 url
  printf '\n================ PRIVATE C1 DESKTOP ================\n' >&2
  if [[ "${CODESPACES:-}" == true && -n "${CODESPACE_NAME:-}" ]]; then
    url=$(c1_codespace_url "$C1_NOVNC_PORT")
    printf ' 1. In VS Code open the PORTS tab, find port %s (noVNC), confirm Visibility = Private,\n' "$C1_NOVNC_PORT" >&2
    printf '    then click the globe icon. Or open:\n    %s\n' "$url" >&2
  else
    printf ' 1. Not in Codespaces. Tunnel the loopback port, e.g. ssh -L %s:127.0.0.1:%s <host>,\n' "$C1_NOVNC_PORT" "$C1_NOVNC_PORT" >&2
    printf '    then open http://localhost:%s/vnc.html?autoconnect=1&resize=remote\n' "$C1_NOVNC_PORT" >&2
  fi
  printf ' 2. One-time VNC password for THIS session only:  %s\n' "$pass" >&2
  printf '    (not saved anywhere; it stops working when the session ends)\n' >&2
  printf ' 3. Keep this terminal open. Follow the prompts here; act in the desktop.\n' >&2
  printf '====================================================\n' >&2
}

session() {
  local offline_only=$1 interactive=0 pass port_private=not-started answer
  [[ -t 0 && -t 2 ]] && interactive=1
  if (( ! offline_only && ! interactive )); then
    c1_warn 'no interactive terminal: supervised phases cannot run; continuing offline-only'
    offline_only=1
  fi
  scrub_env
  case $(c1_session_status "$ROOT") in
    active) c1_err "another C1 session is active; finish it or run: $0 cleanup"; return 1 ;;
    stale)  c1_warn 'recovering from an interrupted session'; c1_cleanup_all "$ROOT" ;;
  esac
  local start; start=$(c1_utc)
  preflight
  install_deps
  S=$(c1_session_new "$ROOT")
  export C1_HARNESS_SESSION=$S
  trap on_exit EXIT
  trap 'exit 130' INT TERM HUP
  local pwver bver
  pwver=$(node -p "require('$FE/node_modules/@playwright/test/package.json').version")
  bver=$(cd "$FE" && TMPDIR="$S/tmp" node --input-type=module -e \
    "import { chromium } from '@playwright/test'; const b = await chromium.launch(); console.log(b.version()); await b.close()")
  # --- automated offline phases (approved suite, unmodified) ---------------
  run_phase static-mutation "$FE" node e2e/c1-mutation-check.mjs
  run_phase offline "$FE" pw_env offline npx --no-install playwright test --config=playwright.config.mjs --reporter=list,json
  run_phase unconfigured "$FE" pw_env unconfigured C1_UNCONFIGURED=1 npx --no-install playwright test --config=playwright.config.mjs --reporter=list,json
  # --- supervised phases ----------------------------------------------------
  if (( offline_only )); then
    skip_phase consent-denial; skip_phase live; skip_phase cancelled-logout
  else
    pass=$(c1_gen_password)
    c1_start_desktop "$S" "$C1_NOVNC_PORT" "$pass"
    if [[ "${CODESPACES:-}" == true ]]; then
      c1_ensure_private_port "$C1_NOVNC_PORT" 1
      if [[ "$(c1_port_visibility "$C1_NOVNC_PORT" 2>/dev/null || true)" == private ]]; then port_private=verified-gh; else port_private=operator-confirmed; fi
    else
      port_private=operator-confirmed
    fi
    print_access "$pass"; pass=''
    read -r -p "Press Enter once the desktop is visible in your browser: " answer
    while :; do
      read -r -p "Public SPA client ID (GUID; NOT a secret, not saved): " CLIENT_ID
      [[ "$CLIENT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] && break
      c1_warn 'not a GUID; try again (Ctrl-C aborts and cleans up)'
    done
    stage_supplemental
    if ask_phase consent-denial 'STEP A — Consent denial' \
'Use a test account that has NOT yet consented (or revoke consent first at https://myapps.microsoft.com).
In the desktop: sign in; on the PERMISSIONS/CONSENT prompt click Cancel. Then click Resume (▶) in the
Playwright Inspector. If no consent prompt appears, just close the popup and Resume: the row is INCOMPLETE.'; then
      run_supplemental consent-denial
    fi
    if ask_phase live 'STEP B — Approved LIVE suite: initial consent + sign-in, then cancelled login' \
'Test 1: complete Microsoft sign-in, MFA and initial consent in the popup, then Resume. Automated checks
run. When it clicks Sign out, complete the Microsoft sign-out popup, then Resume.
Test 2: a sign-in popup opens; CLOSE it without signing in, then Resume.'; then
      run_phase live "$FE" pw_env live C1_LIVE=1 C1_EXPECTED_SHA="$PW" VITE_ENTRA_SPA_CLIENT_ID="$CLIENT_ID" \
        npx --no-install playwright test --config=playwright.config.mjs --headed --workers=1 --reporter=list,json
    fi
    if ask_phase cancelled-logout 'STEP C — Cancelled logout' \
'Sign in normally in the popup, then Resume. The harness clicks Sign out: when the Microsoft
"Pick an account to sign out" popup appears, CLOSE it (X) without choosing an account, then Resume.'; then
      run_supplemental cancelled-logout
    fi
    CLIENT_ID=''
  fi
  # --- sanitized evidence -----------------------------------------------------
  {
    printf 'AUTH_SHA=%s\nPW_SHA=%s\nHARNESS_SHA=%s\nHARNESS_CLEAN=true\n' "$AUTH" "$PW" "$(git -C "$REPO" rev-parse HEAD)"
    printf 'CHECKOUT_VERIFIED=PASS\nCHECKS=%s\n' "$(IFS=,; echo "${CHECKS[*]}")"
    printf 'PLAYWRIGHT_VERSION=%s\nBROWSER_VERSION=%s\nNODE_VERSION=%s\n' "$pwver" "$bver" "$(node --version)"
    printf 'SESSION_START=%s\nSESSION_END=%s\n' "$start" "$(c1_utc)"
    printf 'MODE=%s\nCODESPACES=%s\nPORT_PRIVATE=%s\nLOCALHOST=PASS\n' \
      "$( (( offline_only )) && echo offline-only || echo supervised)" "$([[ "${CODESPACES:-}" == true ]] && echo true || echo false)" "$port_private"
  } >"$S/meta"
  # Exact-head re-check AFTER the run: nothing may have changed the checkout or harness.
  c1_verify_clean_tree "$WT" "$PW" || sed -i 's/^CHECKOUT_VERIFIED=PASS$/CHECKOUT_VERIFIED=FAIL/' "$S/meta"
  c1_verify_harness_clean "$REPO" "$HARNESS_REL" || sed -i 's/^HARNESS_CLEAN=true$/HARNESS_CLEAN=false/' "$S/meta"
  local outv; outv=$(node "$HERE/lib/c1-evidence.mjs" --session="$S" --out="$EVIDENCE_DIR")
  local ev rp verdict; ev=$(sed -n 1p <<<"$outv"); rp=$(sed -n 2p <<<"$outv"); verdict=$(sed -n 3p <<<"$outv")
  # --- cleanup and verification ---------------------------------------------
  local sess=$S; S=''
  unset C1_HARNESS_SESSION
  c1_cleanup_session "$ROOT" "$sess"
  local cv='PASS — desktop and test processes stopped, ports 5173/6080 closed, session directory (VNC socket, X cookie, raw reports, browser profiles) deleted'
  c1_verify_cleanup "$sess" || cv='FAIL — see terminal; run c1-session.sh cleanup'
  sed -i "s|@@C1_CLEANUP_VERDICT@@|$cv ($(c1_utc))|" "$ev" "$rp"
  printf '\nC1 harness summary: %s\nEvidence:        %s\nReview package:  %s\n' "$verdict" "$ev" "$rp"
  printf 'Paste the review package into a SEPARATE Claude session for independent security review.\n'
  [[ "$cv" == PASS* ]]
}

main() {
  local cmd=${1:-run} offline=0
  [[ "$cmd" == --offline-only ]] && { cmd=run; offline=1; }
  [[ "${2:-}" == --offline-only ]] && offline=1
  case $cmd in
    run) session "$offline" ;;
    preflight) scrub_env; preflight; install_deps; c1_log "preflight PASS: ${CHECKS[*]}" ;;
    cleanup) c1_cleanup_all "$ROOT"; c1_port_free "$C1_NOVNC_PORT" && c1_log 'no harness sessions remain' ;;
    purge) c1_cleanup_all "$ROOT"
           if [[ -e "$WT" ]]; then git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || c1_safe_rm "$WT" "$CACHE"; fi
           git -C "$REPO" worktree prune; rm -f "$CACHE"/deps-ok-*; c1_log 'purged cached approved worktree' ;;
    selftest) bash "$HERE/tests/test_c1_lib.sh" && node --test "$HERE"/tests/*.test.mjs ;;
    -h|--help|help) usage ;;
    *) usage; return 2 ;;
  esac
}

main "$@"

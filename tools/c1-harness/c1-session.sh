#!/usr/bin/env bash
# C1 Microsoft Entra validation harness — ONE COMMAND, development only.
#
#   bash tools/c1-harness/c1-session.sh              # full supervised session (Codespaces only)
#   bash tools/c1-harness/c1-session.sh --offline-only
#   bash tools/c1-harness/c1-session.sh preflight | cleanup | purge | selftest
#
# Runs the approved PR #60 Playwright suite UNMODIFIED from a detached, clean
# worktree at the pinned approved SHA, inside a private Xvfb/noVNC desktop
# reachable only through Codespaces private port forwarding that is verified
# technically (fail closed) before the desktop is served. It never stores
# Microsoft credentials, tokens, cookies or browser artifacts, never deploys,
# never touches Azure, and never certifies C1 by itself.
set -Eeuo pipefail
umask 077

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
LIB="$HERE/lib/c1-lib.sh"
# shellcheck source=lib/c1-lib.sh
source "$LIB"

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
PROD_PENDING=unknown

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# Variables that could change test semantics, the browser binary, or leak data
# are removed here; test phases additionally run under an env -i allowlist (L-4).
scrub_env() {
  unset C1_LIVE C1_UNCONFIGURED C1_MUTATION_MODE C1_EXPECTED_SHA C1_SUPERVISED_CASE \
    VITE_ENTRA_SPA_CLIENT_ID VITE_C1_SHA VITE_C1_DIRTY PWDEBUG DEBUG \
    PLAYWRIGHT_JSON_OUTPUT_NAME PLAYWRIGHT_JSON_OUTPUT_FILE PWTEST_SKIP_TEST_OUTPUT \
    PLAYWRIGHT_HTML_REPORT NODE_OPTIONS NODE_PATH CI __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS 2>/dev/null || true
  local v
  for v in $(compgen -e); do
    case $v in PLAYWRIGHT_*|PW_*|npm_config_*|NPM_CONFIG_*) unset "$v" ;; esac
  done
}

check() { CHECKS+=("$1"); }

# N-1: every check aborts on failure. (An `A && check B` list is exempt from
# errexit, so a failed verification would silently continue.)
preflight() {
  local t major
  for t in git node npm sha256sum setsid realpath awk getent mktemp curl python3 od; do
    command -v "$t" >/dev/null || { c1_err "required tool missing: $t"; return 1; }
  done
  major=$(node -p 'process.versions.node.split(".")[0]')
  (( major >= 20 )) || { c1_err "Node.js >= 20 required (found $(node --version))"; return 1; }
  c1_verify_harness_clean "$REPO" "$HARNESS_REL" || return 1; check harness_clean
  c1_fetch_commit "$REPO" "$AUTH" && c1_fetch_commit "$REPO" "$PW" || { c1_err 'cannot obtain approved commits'; return 1; }
  c1_verify_commits "$REPO" "$AUTH" "$PW" "$C1_AUTH_TO_PW_FILES" || return 1; check auth_ancestor_and_approved_diff
  PROD_PENDING=$(c1_verify_prod_pending "$REPO" "$AUTH" "$PW" "$C1_AUTH_TO_PW_FILES" "$C1_PROD_REVIEW_PENDING") || return 1
  check production_review_status_recorded
  c1_prepare_worktree "$REPO" "$PW" "$WT" || return 1; check exact_head_clean_worktree
  c1_check_vite_host_guard "$WT" || return 1; check vite_host_guard
  c1_check_localhost || return 1; check localhost_loopback_only
  c1_port_free "$C1_APP_PORT" || { c1_err "port $C1_APP_PORT in use; stop the existing dev server (fresh server is mandatory)"; return 1; }
  c1_port_free "$C1_NOVNC_PORT" || { c1_err "port $C1_NOVNC_PORT in use; run: c1-session.sh cleanup"; return 1; }
  check ports_free
}

install_deps() {
  mkdir -p "$CACHE"
  c1_install_apt_packages || return 1
  command -v ss >/dev/null || { c1_err 'ss (iproute2) is required for listener verification'; return 1; }
  c1_install_node_deps "$FE" "$CACHE" || return 1
  c1_verify_clean_tree "$WT" "$PW" || return 1; check clean_after_dependency_install
}

# Cleanup runs on EVERY exit path and is verified; a failed cleanup is never hidden.
on_exit() {
  local rc=$?
  trap - EXIT INT TERM HUP
  if [[ -n "$S" && -d "$S" ]]; then
    local s=$S; S=''
    c1_log 'cleaning up: stopping desktop/test processes, closing listeners, deleting session files'
    if ! c1_cleanup_session "$ROOT" "$s" || ! c1_verify_cleanup "$s"; then
      c1_err 'CLEANUP FAILED: harness processes, listeners or session files may remain; run: c1-session.sh cleanup'
      (( rc != 0 )) || rc=3
    fi
  fi
  exit "$rc"
}

# pw_env ID [VAR=VALUE...] CMD...  — minimal allowlisted environment (L-4)
pw_env() {
  local id=$1; shift
  local -a e
  mapfile -d '' -t e < <(c1_clean_env)
  "${e[@]}" TMPDIR="$S/tmp" PLAYWRIGHT_JSON_OUTPUT_NAME="$S/reports/$id.json" "$@"
}

clean_node() { local -a e; mapfile -d '' -t e < <(c1_clean_env); "${e[@]}" TMPDIR="$S/tmp" node "$@"; }

# Terminal output is never raw Playwright/MSAL text (L-2/Q7, L-7): phase output
# goes to the 0700 session directory. When a JSON report exists (pass OR fail)
# only the per-test summary is shown, so failing locator/DOM text (e.g. an
# app's "Signed in: <display name>") never reaches the terminal. Only a phase
# without a report (startup/config failure, non-Playwright step) shows
# sanitizer-approved lines, with identity-bearing lines withheld.
show_phase() {
  local id=$1 rc=$2 out=$3
  if [[ -f "$S/reports/$id.json" ]]; then
    clean_node "$HERE/lib/c1-evidence.mjs" --summarize="$S/reports/$id.json" >&2 || true
    [[ "$rc" == 0 ]] || c1_log "phase $id: failure details stay in the private session directory (summary only, L-7)"
  else
    clean_node "$HERE/lib/c1-evidence.mjs" --filter="$out" --max=40 >&2 || true
  fi
}

# run_phase ID DIR CMD...   (never aborts the session on test failure)
run_phase() {
  local id=$1 dir=$2 start rc out; shift 2
  out="$S/log/phase-$id.out"
  start=$(c1_utc)
  c1_log "phase $id: starting ($start)"
  set +e
  (cd "$dir" && "$@") >"$out" 2>&1
  rc=$?
  set -e
  printf '%s|ran|%s|%s|%s\n' "$id" "$start" "$(c1_utc)" "$rc" >>"$S/phases"
  show_phase "$id" "$rc" "$out"
  c1_log "phase $id: finished with exit $rc"
}

skip_phase() { printf '%s|skipped|-|-|-\n' "$1" >>"$S/phases"; c1_log "phase $1: not executed (INCOMPLETE)"; }

ask_phase() {
  local id=$1 answer
  printf '\n==== %s ====\n%s\n' "$2" "$3" >&2
  read -r -p "Press Enter to start, or type s to skip (row becomes INCOMPLETE): " answer
  [[ "$answer" != s && "$answer" != S ]] || { skip_phase "$id"; return 1; }
}

# ask_yes_no QUESTION -> prints yes | no (only an exact "yes" counts)
ask_yes_no() {
  local answer
  read -r -p "$1 Type yes or no: " answer
  if [[ "$answer" == yes ]]; then echo yes; else echo no; fi
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
    --headed --workers=1 --reporter=json
}

print_access() {
  local pass=$1
  printf '\n================ PRIVATE C1 DESKTOP ================\n' >&2
  printf ' Port %s privacy was verified technically: an unauthenticated probe could NOT\n' "$C1_NOVNC_PORT" >&2
  printf ' reach it and a GitHub-authenticated probe could. It is re-checked continuously.\n' >&2
  printf ' 1. Open (you will be asked to sign in to GitHub if needed):\n    %s\n' "$(c1_codespace_url "$C1_NOVNC_PORT")" >&2
  printf ' 2. One-time VNC password for THIS session only:  %s\n' "$pass" >&2
  printf '    (not saved anywhere; it stops working when the session ends)\n' >&2
  printf ' 3. Keep this terminal open. Follow the prompts here; act in the desktop.\n' >&2
  printf '====================================================\n' >&2
}

# privacy_gate -> 0 only if the noVNC port is still verified private (and, on
# the gh path, gh still reads back "private"). Otherwise the shared teardown
# (L-9: marker, TERM -> wait -> KILL, port 6080 verified closed) runs; if the
# port cannot be verified closed the session aborts and full cleanup runs.
privacy_gate() {
  [[ "$PORT_PRIVATE" == verified-* ]] || return 1
  local rc=0
  c1_privacy_gate "$S" "$C1_NOVNC_PORT" "$BASE" "$HDR" "$WS" "$WSST" "$REQ_GH" || rc=$?
  (( rc == 0 )) && return 0
  c1_err "port $C1_NOVNC_PORT privacy could no longer be verified (${C1_GATE_REASON:-unverified}); the desktop was stopped"
  PORT_PRIVATE=failed
  if (( rc == 2 )); then
    c1_err 'noVNC teardown could not be verified; aborting the session (full cleanup and verification follow)'
    exit 4
  fi
  return 1
}

# fail_desktop -> stop noVNC (if started) and mark the desktop failed; aborts on an unverified teardown
fail_desktop() {
  PORT_PRIVATE=failed
  if [[ -n "$WS" ]] && ! c1_teardown_novnc "$S" "$C1_NOVNC_PORT" "$WS" "$WSST" "${1:-unverified}"; then
    c1_err 'noVNC teardown could not be verified; aborting the session (full cleanup and verification follow)'
    exit 4
  fi
  return 1
}

# start_private_desktop PASSWORD -> sets PORT_PRIVATE, WS, WSST
start_private_desktop() {
  local pass=$1 vis owner
  BASE=$(c1_codespace_base "$C1_NOVNC_PORT") || { c1_err 'CODESPACE_NAME / forwarding domain unavailable'; return 1; }
  [[ -n "${GITHUB_TOKEN:-}" ]] || { c1_err 'GITHUB_TOKEN is not available in this Codespace terminal'; return 1; }
  HDR="$S/probe-token.hdr"
  # Called as `if ! start_private_desktop` (errexit is OFF in here): every step is checked explicitly.
  c1_write_header_file "$HDR" X-Github-Token "$GITHUB_TOKEN" || return 1
  c1_write_header_file "$S/api-token.hdr" Authorization "token $GITHUB_TOKEN" || return 1
  c1_start_display_vnc "$S" "$pass" || return 1
  c1_log "verifying Codespaces port $C1_NOVNC_PORT privacy with a decoy (no desktop behind it yet)"
  if command -v gh >/dev/null 2>&1 && [[ -n "${CODESPACE_NAME:-}" ]]; then
    gh codespace ports visibility "$C1_NOVNC_PORT:private" -c "$CODESPACE_NAME" >/dev/null 2>&1 || true
  fi
  c1_verify_private_with_decoy "$S" "$C1_NOVNC_PORT" "$BASE" "$HDR" || { PORT_PRIVATE=failed; return 1; }
  # "Private to organization" also requires GitHub sign-in, so the probe alone
  # cannot exclude it. Accept only a gh read-back of "private", or a
  # personal (User-owned) repository, where organization visibility does not apply.
  vis=$(c1_gh_visibility "$C1_NOVNC_PORT" || true)
  if [[ -n "$vis" && "$vis" != private ]]; then c1_err "gh reports port visibility '$vis'"; PORT_PRIVATE=failed; return 1; fi
  if [[ "$vis" != private ]]; then
    owner=$(c1_repo_owner_type "${GITHUB_REPOSITORY:-}" "$S/api-token.hdr" || true)
    [[ "$owner" == User ]] || { c1_err "cannot exclude organization-wide port visibility (repository owner type '${owner:-unknown}', gh read-back unavailable)"; PORT_PRIVATE=failed; return 1; }
  fi
  # L-10: on the gh path, gh must keep reading back "private" (org visibility
  # still demands GitHub sign-in, so only gh can see a later change to it).
  if [[ "$vis" == private ]]; then REQ_GH=1; else REQ_GH=0; fi
  local wrc=0
  WS=$(c1_start_websockify "$S" "$C1_NOVNC_PORT") || wrc=$?
  if (( wrc )); then
    WS=''
    (( wrc != 2 )) || { c1_err 'noVNC teardown could not be verified; aborting the session (full cleanup and verification follow)'; exit 4; }
    PORT_PRIVATE=failed; return 1
  fi
  WSST=$(c1_proc_start "$WS"); [[ -n "$WSST" ]] || { fail_desktop websockify-start; return 1; }
  if ! c1_verify_private_novnc "$BASE" "$HDR"; then
    c1_err "noVNC privacy re-check failed ($C1_PRIVACY_STATE: $C1_PRIVACY_DETAIL)"
    fail_desktop "$C1_PRIVACY_STATE"; return 1
  fi
  if (( REQ_GH )) && ! c1_gh_private "$C1_NOVNC_PORT"; then
    c1_err "gh no longer reads back port $C1_NOVNC_PORT as private (${C1_GH_VIS:-unavailable})"
    fail_desktop gh-visibility; return 1
  fi
  if (( REQ_GH )); then PORT_PRIVATE=verified-probe-gh; else PORT_PRIVATE=verified-probe-user-repo; fi
  # shellcheck disable=SC2016
  # L-6: 0.2 s poll plus bounded 0.4 s probes and fast teardown target <=2.5 s.
  # The monitor checks noVNC privacy before each supervised phase as well.
  c1_spawn "$S" privacy-monitor bash -c 'source "$1"; c1_privacy_monitor_loop "$2" "$3" "$4" "$5" "$6" "" 0.2 "$7" "$8"' \
    _ "$LIB" "$S" "$BASE" "$HDR" "$WS" "$WSST" "$C1_NOVNC_PORT" "$REQ_GH" >/dev/null || { fail_desktop monitor-start; return 1; }
}

session() {
  local offline_only=$1 interactive=0 pass answer
  PORT_PRIVATE=not-started BASE='' HDR='' WS='' WSST='' REQ_GH=0
  local att_prior=not-asked att_prompt=not-asked bhver=not-launched
  [[ -t 0 && -t 2 ]] && interactive=1
  if (( ! offline_only && ! interactive )); then
    c1_warn 'no interactive terminal: supervised phases cannot run; continuing offline-only'
    offline_only=1
  fi
  if (( ! offline_only )) && [[ "${CODESPACES:-}" != true ]]; then
    c1_err 'supervised mode runs only in a GitHub Codespace, where port privacy can be verified; use --offline-only'
    return 1
  fi
  scrub_env
  case $(c1_session_status "$ROOT") in
    active) c1_err "another C1 session is active; finish it or run: $0 cleanup"; return 1 ;;
    stale)  c1_warn 'recovering from an interrupted session'; c1_cleanup_all "$ROOT" ;;
  esac
  local start; start=$(c1_utc)
  preflight || { c1_err 'preflight failed; nothing was started'; return 1; }
  install_deps || { c1_err 'dependency installation or post-install verification failed'; return 1; }
  S=$(c1_session_new "$ROOT")
  export C1_HARNESS_SESSION=$S TMPDIR="$S/tmp"
  trap on_exit EXIT
  trap 'exit 130' INT TERM HUP
  c1_start_watchdog "$ROOT" "$S" "$$" "$LIB"
  local pwver bver expb expbh
  pwver=$(node -p "require('$FE/node_modules/@playwright/test/package.json').version")
  expb=$(c1_expected_browser "$FE" chromium-headless-shell)
  expbh=$(c1_expected_browser "$FE" chromium)
  bver=$(cd "$FE" && clean_node --input-type=module -e \
    "import { chromium } from '@playwright/test'; const b = await chromium.launch(); console.log(b.version()); await b.close()")
  # --- automated offline phases (approved suite, unmodified) ---------------
  run_phase static-mutation "$FE" pw_env static-mutation node e2e/c1-mutation-check.mjs
  if [[ -f "$FE/e2e/run-browser-mutations.mjs" ]]; then
    run_phase browser-mutation "$FE" pw_env browser-mutation node e2e/run-browser-mutations.mjs
  else
    skip_phase browser-mutation
  fi
  run_phase offline "$FE" pw_env offline npx --no-install playwright test --config=playwright.config.mjs --reporter=json
  run_phase unconfigured "$FE" pw_env unconfigured C1_UNCONFIGURED=1 npx --no-install playwright test --config=playwright.config.mjs --reporter=json
  # --- supervised phases ----------------------------------------------------
  if (( offline_only )); then
    skip_phase consent-denial; skip_phase live; skip_phase cancelled-logout
  else
    pass=$(c1_gen_password)
    if ! start_private_desktop "$pass"; then
      pass=''
      c1_err 'private desktop NOT served; supervised rows stay INCOMPLETE and the privacy row FAILS'
      skip_phase consent-denial; skip_phase live; skip_phase cancelled-logout
    else
      bhver=$(cd "$FE" && clean_node --input-type=module -e \
        "import { chromium } from '@playwright/test'; const b = await chromium.launch({ headless: false }); console.log(b.version()); await b.close()" || echo unknown)
      print_access "$pass"; pass=''
      read -r -p "Press Enter once the desktop is visible in your browser: " answer
      while :; do
        read -r -p "Public SPA client ID (GUID; NOT a secret, not saved): " CLIENT_ID
        [[ "$CLIENT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] && break
        c1_warn 'not a GUID; try again (Ctrl-C aborts and cleans up)'
      done
      stage_supplemental
      if privacy_gate && ask_phase consent-denial 'STEP A — Consent denial' \
'Use a test account that has NEVER consented to this app (or revoke its consent first at
https://myapps.microsoft.com). In the desktop: sign in; on the Microsoft PERMISSIONS (consent)
prompt click Cancel. Then click Resume (▶) in the Playwright Inspector.'; then
        att_prior=$(ask_yes_no 'Attestation 1/2: this test account has NO existing consent to this app.')
        run_supplemental consent-denial
        att_prompt=$(ask_yes_no 'Attestation 2/2: Microsoft showed the PERMISSIONS (consent) prompt and you clicked Cancel on it.')
      elif ! grep -q '^consent-denial|' "$S/phases"; then skip_phase consent-denial; fi
      if privacy_gate && ask_phase live 'STEP B — Approved LIVE suite: initial consent + sign-in, then cancelled login' \
'Test 1: complete Microsoft sign-in, MFA and initial consent in the popup, then Resume. Automated checks
run. When it clicks Sign out, complete the Microsoft sign-out popup, then Resume.
Test 2: a sign-in popup opens; CLOSE it without signing in, then Resume.'; then
        run_phase live "$FE" pw_env live C1_LIVE=1 C1_EXPECTED_SHA="$PW" VITE_ENTRA_SPA_CLIENT_ID="$CLIENT_ID" \
          npx --no-install playwright test --config=playwright.config.mjs --headed --workers=1 --reporter=json
      elif ! grep -q '^live|' "$S/phases"; then skip_phase live; fi
      if privacy_gate && ask_phase cancelled-logout 'STEP C — Cancelled logout' \
'Sign in normally in the popup, then Resume. The harness clicks Sign out: when the Microsoft
"Pick an account to sign out" popup appears, CLOSE it (X) without choosing an account, then Resume.'; then
        run_supplemental cancelled-logout
      elif ! grep -q '^cancelled-logout|' "$S/phases"; then skip_phase cancelled-logout; fi
      CLIENT_ID=''
      [[ ! -e "$S/privacy-failed" ]] || PORT_PRIVATE=failed
    fi
  fi
  # --- sanitized evidence -----------------------------------------------------
  {
    printf 'AUTH_SHA=%s\nPW_SHA=%s\nHARNESS_SHA=%s\nHARNESS_CLEAN=true\n' "$AUTH" "$PW" "$(git -C "$REPO" rev-parse HEAD)"
    printf 'CHECKOUT_VERIFIED=PASS\nCHECKS=%s\n' "$(IFS=,; echo "${CHECKS[*]}")"
    printf 'PLAYWRIGHT_VERSION=%s\nEXPECTED_PLAYWRIGHT_VERSION=%s\n' "$pwver" "$C1_PLAYWRIGHT_VERSION"
    printf 'BROWSER_VERSION=%s\nEXPECTED_BROWSER_VERSION=%s\n' "$bver" "$expb"
    printf 'BROWSER_HEADED_VERSION=%s\nEXPECTED_HEADED_BROWSER_VERSION=%s\nNODE_VERSION=%s\n' "$bhver" "$expbh" "$(node --version)"
    printf 'SESSION_START=%s\nSESSION_END=%s\n' "$start" "$(c1_utc)"
    printf 'MODE=%s\nCODESPACES=%s\nPORT_PRIVATE=%s\nLOCALHOST=PASS\n' \
      "$( (( offline_only )) && echo offline-only || echo supervised)" "$([[ "${CODESPACES:-}" == true ]] && echo true || echo false)" "$PORT_PRIVATE"
    printf 'ATTEST_NO_PRIOR_CONSENT=%s\nATTEST_CONSENT_PROMPT_CANCELLED=%s\n' "$att_prior" "$att_prompt"
    printf 'PROD_REVIEW_PENDING=%s\n' "$PROD_PENDING"
  } >"$S/meta"
  # Exact-head re-check AFTER the run: nothing may have changed the checkout or harness.
  c1_verify_clean_tree "$WT" "$PW" || sed -i 's/^CHECKOUT_VERIFIED=PASS$/CHECKOUT_VERIFIED=FAIL/' "$S/meta"
  c1_verify_harness_clean "$REPO" "$HARNESS_REL" || sed -i 's/^HARNESS_CLEAN=true$/HARNESS_CLEAN=false/' "$S/meta"
  local outv; outv=$(clean_node "$HERE/lib/c1-evidence.mjs" --session="$S" --out="$EVIDENCE_DIR")
  local ev rp verdict; ev=$(sed -n 1p <<<"$outv"); rp=$(sed -n 2p <<<"$outv"); verdict=$(sed -n 3p <<<"$outv")
  # --- cleanup and verification ---------------------------------------------
  local sess=$S; S=''
  unset C1_HARNESS_SESSION
  local cv='PASS — desktop and test processes stopped, ports 5173/6080 closed, session directory (VNC socket, X cookie, probe token files, raw reports, browser profiles) deleted'
  if ! c1_cleanup_session "$ROOT" "$sess" || ! c1_verify_cleanup "$sess"; then cv='FAIL — see terminal; run c1-session.sh cleanup'; fi
  sed -i "s|@@C1_CLEANUP_VERDICT@@|$cv ($(c1_utc))|" "$ev" "$rp"
  [[ "$cv" == PASS* ]] || { verdict=FAIL; sed -i 's/^\*\*Harness summary: [A-Z ]*\.\*\*/**Harness summary: FAIL.**/' "$ev" "$rp"; }
  printf '\nC1 harness summary: %s\nEvidence:        %s\nReview package:  %s\n' "$verdict" "$ev" "$rp"
  printf 'Paste the review package into a SEPARATE Claude session for independent security review.\n'
  # Exit status: non-zero on FAIL or failed cleanup; INCOMPLETE/READY exit 0.
  [[ "$cv" == PASS* && "$verdict" != FAIL ]]
}

main() {
  local sub=${1:-run} offline=0
  [[ "$sub" == --offline-only ]] && { sub=run; offline=1; }
  [[ "${2:-}" == --offline-only ]] && offline=1
  case $sub in
    run) session "$offline" ;;
    preflight) scrub_env; preflight || return 1; install_deps || return 1; c1_log "preflight PASS: ${CHECKS[*]}" ;;
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

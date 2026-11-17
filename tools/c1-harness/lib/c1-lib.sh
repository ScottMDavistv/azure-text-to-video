# shellcheck shell=bash
# shellcheck disable=SC2034  # constants are consumed by c1-session.sh and tests
# C1 validation harness library. Development-only. Sourced by c1-session.sh
# and by the regression tests. Functions take explicit arguments so tests can
# exercise them against fixture repositories; the launcher alone supplies the
# pinned approved values below. There is deliberately NO environment override
# for the approved SHAs.

C1_AUTH_SHA_APPROVED='6b62c05aad50620f60f4d26ad2821b74b939f920'
C1_PW_SHA_APPROVED='79f87cb4d4ddba819da57095da0ec3bcca8cb452'
C1_PLAYWRIGHT_VERSION='1.65.1'
# Exact set of files that may differ between the approved auth commit and the
# approved Playwright commit. Anything else (e.g. src/entraAuth.ts) => reject.
C1_AUTH_TO_PW_FILES='.github/workflows/frontend-quality-gates.yml
frontend/.gitignore
frontend/e2e/README.md
frontend/e2e/TIER1-TIER2.md
frontend/e2e/c1-guards.mjs
frontend/e2e/c1-lockfile-check.mjs
frontend/e2e/c1-mutation-check.mjs
frontend/e2e/c1-mutations.mjs
frontend/e2e/c1-source-policy.mjs
frontend/e2e/entra-c1.spec.mjs
frontend/e2e/entra-tier1.spec.mjs
frontend/e2e/run-browser-mutations.mjs
frontend/playwright.config.mjs
frontend/src/App.tsx
frontend/src/c1BuildInfo.ts'
C1_APT_PACKAGES='xvfb x11vnc novnc websockify fluxbox xauth iproute2'
C1_NOVNC_PORT=6080
C1_APP_PORT=5173
C1_NOVNC_WEB=/usr/share/novnc

c1_log()  { printf '[c1] %s\n' "$*" >&2; }
c1_warn() { printf '[c1] WARNING: %s\n' "$*" >&2; }
c1_err()  { printf '[c1] ERROR: %s\n' "$*" >&2; }
c1_utc()  { date -u +%Y-%m-%dT%H:%M:%SZ; }
c1_is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

# ---------------------------------------------------------------- commits --

# c1_verify_commits REPO AUTH_SHA PW_SHA EXPECTED_FILES
c1_verify_commits() {
  local repo=$1 auth=$2 pw=$3 expected=$4 actual
  c1_is_sha "$auth" && c1_is_sha "$pw" || { c1_err 'approved SHAs must be full 40-hex'; return 1; }
  git -C "$repo" cat-file -e "$auth^{commit}" 2>/dev/null || { c1_err "auth commit $auth not present"; return 1; }
  git -C "$repo" cat-file -e "$pw^{commit}" 2>/dev/null || { c1_err "Playwright commit $pw not present"; return 1; }
  git -C "$repo" merge-base --is-ancestor "$auth" "$pw" || { c1_err 'auth SHA is not an ancestor of the Playwright SHA'; return 1; }
  actual=$(git -C "$repo" diff --no-renames --name-only "$auth" "$pw" | LC_ALL=C sort)
  if [[ "$actual" != "$(printf '%s\n' "$expected" | LC_ALL=C sort)" ]]; then
    c1_err 'files changed between auth and Playwright SHAs differ from the approved set:'
    diff <(printf '%s\n' "$expected" | LC_ALL=C sort) <(printf '%s\n' "$actual") >&2 || true
    return 1
  fi
}

# c1_fetch_commit REPO SHA  (fetch an exact commit only if it is missing)
c1_fetch_commit() {
  local repo=$1 sha=$2
  git -C "$repo" cat-file -e "$sha^{commit}" 2>/dev/null && return 0
  c1_log "fetching approved commit ${sha:0:12} from origin"
  git -C "$repo" fetch --quiet --no-tags origin "$sha" && git -C "$repo" cat-file -e "$sha^{commit}"
}

# c1_verify_clean_tree DIR EXPECTED_SHA  (exact HEAD, no changes, no hidden index)
c1_verify_clean_tree() {
  local dir=$1 sha=$2 head dirty hidden
  head=$(git -C "$dir" rev-parse HEAD 2>/dev/null) || { c1_err "$dir is not a git checkout"; return 1; }
  [[ "$head" == "$sha" ]] || { c1_err "checkout HEAD ${head:0:12} != approved ${sha:0:12}"; return 1; }
  dirty=$(git -C "$dir" status --porcelain --untracked-files=all)
  [[ -z "$dirty" ]] || { c1_err 'approved checkout has modified or untracked files'; printf '%s\n' "${dirty:0:2000}" >&2; return 1; }
  hidden=$(git -C "$dir" ls-files -v | grep -E '^[a-zS] ' || true)
  [[ -z "$hidden" ]] || { c1_err 'approved checkout has skip-worktree/assume-unchanged entries'; return 1; }
  git -C "$dir" diff --quiet "$sha" -- || { c1_err 'worktree content differs from approved commit'; return 1; }
}

# c1_prepare_worktree REPO SHA PATH  (create or validate a detached worktree)
c1_prepare_worktree() {
  local repo=$1 sha=$2 path=$3
  if [[ -e "$path" ]]; then
    if git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
       && [[ "$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" -ef "$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)" ]]; then
      c1_verify_clean_tree "$path" "$sha" && return 0
      c1_err "cached approved worktree $path is modified; run: c1-session.sh purge"
      return 1
    fi
    c1_warn "removing unregistered/corrupt cached worktree $path"
    c1_safe_rm "$path" "$(dirname "$path")" || return 1
    git -C "$repo" worktree prune
  fi
  mkdir -p "$(dirname "$path")"
  git -C "$repo" worktree add --quiet --detach "$path" "$sha" || return 1
  c1_verify_clean_tree "$path" "$sha"
}

# c1_verify_harness_clean REPO HARNESS_RELPATH
c1_verify_harness_clean() {
  local repo=$1 rel=$2 dirty hidden
  dirty=$(git -C "$repo" status --porcelain --untracked-files=all -- "$rel")
  [[ -z "$dirty" ]] || { c1_err "harness files are modified ($rel); evidence would be untrusted"; return 1; }
  # git status trusts skip-worktree/assume-unchanged flags; reject them and
  # compare file content against HEAD directly (L-1).
  hidden=$(git -C "$repo" ls-files -v -- "$rel" | grep -E '^[a-zS] ' || true)
  [[ -z "$hidden" ]] || { c1_err "harness files carry skip-worktree/assume-unchanged flags ($rel)"; return 1; }
  git -C "$repo" diff --quiet --no-ext-diff HEAD -- "$rel" || { c1_err "harness content differs from HEAD ($rel)"; return 1; }
  local f top; top=$(git -C "$repo" rev-parse --show-toplevel)
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ "$(git -C "$repo" hash-object -- "$top/$f" 2>/dev/null)" == "$(git -C "$repo" rev-parse "HEAD:$f" 2>/dev/null)" ]] \
      || { c1_err "harness file $f differs from HEAD"; return 1; }
  done < <(git -C "$repo" ls-files --full-name -- "$rel")
}

# --------------------------------------------------------------- preflight --

# c1_check_localhost [RESOLVER_OUTPUT]  localhost must resolve only to loopback
c1_check_localhost() {
  local out=${1-} addr ok=0
  [[ -n "$out" ]] || out=$(getent ahosts localhost 2>/dev/null | awk '{print $1}' | sort -u)
  [[ -n "$out" ]] || { c1_err 'localhost does not resolve'; return 1; }
  while read -r addr; do
    [[ -z "$addr" ]] && continue
    if [[ "$addr" =~ ^127\.[0-9]+\.[0-9]+\.[0-9]+$ || "$addr" == '::1' ]]; then ok=1
    else c1_err "localhost resolves to non-loopback address $addr"; return 1; fi
  done <<<"$out"
  (( ok ))
}

# c1_port_free PORT  -> 0 if nothing listens on the TCP port (any address)
c1_port_free() {
  local port=$1
  if command -v ss >/dev/null 2>&1; then
    [[ -z "$(ss -Hltn "sport = :$port" 2>/dev/null)" ]]
  else
    ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

# c1_listeners_for_pids PIDS...  -> "addr:port" lines for TCP listeners of pids
c1_listeners_for_pids() {
  local p
  ss -Hltnp 2>/dev/null | while read -r _ _ _ local _ users; do
    for p in "$@"; do
      if [[ "$users" == *"pid=$p,"* ]]; then printf '%s\n' "$local"; fi
    done
  done | sort -u
  return 0
}

# c1_assert_loopback_only "addr:port" lines on stdin
c1_assert_loopback_only() {
  local l bad=0
  while read -r l; do
    [[ -z "$l" ]] && continue
    case "$l" in
      127.*:*|'[::1]':*) ;;
      *) c1_err "non-loopback listener detected: $l"; bad=1 ;;
    esac
  done
  (( bad == 0 ))
}

c1_missing_apt_packages() {
  local p
  for p in $C1_APT_PACKAGES; do
    [[ "$(dpkg-query -W -f='${Status}' "$p" 2>/dev/null)" == *'install ok installed'* ]] || printf '%s ' "$p"
  done
}

c1_install_apt_packages() {
  local missing; missing=$(c1_missing_apt_packages)
  [[ -z "$missing" ]] && { c1_log 'desktop packages already installed'; return 0; }
  c1_log "installing approved development packages: $missing"
  local sudo=''; [[ $(id -u) -eq 0 ]] || sudo='sudo -n'
  # shellcheck disable=SC2086
  { $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $missing \
    || { $sudo apt-get update -qq && $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $missing; }; } >/dev/null
  missing=$(c1_missing_apt_packages)
  [[ -z "$missing" ]] || { c1_err "packages still missing: $missing"; return 1; }
}

# c1_install_node_deps FRONTEND_DIR CACHE_DIR
c1_install_node_deps() {
  local fe=$1 cache=$2 lockhash marker ver
  lockhash=$(sha256sum "$fe/package-lock.json" | cut -d' ' -f1)
  marker="$fe/node_modules/.c1-harness-lock-sha256"
  if [[ ! -f "$marker" || "$(cat "$marker")" != "$lockhash" ]]; then
    c1_log 'installing frontend dependencies from approved lockfile (npm ci)'
    (cd "$fe" && npm ci --no-audit --no-fund --loglevel=error) || return 1
    printf '%s' "$lockhash" >"$marker"
  else
    c1_log 'frontend dependencies match approved lockfile'
  fi
  ver=$(node -p "require('$fe/node_modules/@playwright/test/package.json').version" 2>/dev/null || true)
  if [[ "$ver" != "$C1_PLAYWRIGHT_VERSION" ]]; then
    c1_log "installing @playwright/test@$C1_PLAYWRIGHT_VERSION (no-save, ignore-scripts, as approved README)"
    (cd "$fe" && npm install --no-save --ignore-scripts --no-audit --no-fund --loglevel=error "@playwright/test@$C1_PLAYWRIGHT_VERSION") || return 1
  fi
  (cd "$fe" && npx --no-install playwright install chromium) || return 1
  if [[ ! -f "$cache/deps-ok-$C1_PLAYWRIGHT_VERSION" ]]; then
    c1_log 'installing Chromium system dependencies'
    local sudo=''; [[ $(id -u) -eq 0 ]] || sudo='sudo -n'
    (cd "$fe" && $sudo env "PATH=$PATH" npx --no-install playwright install-deps chromium >/dev/null) || return 1
    touch "$cache/deps-ok-$C1_PLAYWRIGHT_VERSION"
  fi
  # Restore any tree changes npm might make (it must not touch tracked files).
  [[ -z "$(git -C "$fe" status --porcelain --untracked-files=all)" ]] || { c1_err 'dependency install modified the approved checkout'; return 1; }
  return 0
}

# ----------------------------------------------------------------- session --

# c1_safe_rm PATH PARENT  -> rm -rf only if PATH is strictly inside PARENT
c1_safe_rm() {
  local path=$1 parent=$2 rp pp
  [[ -e "$path" ]] || return 0
  rp=$(realpath -e -- "$path") && pp=$(realpath -e -- "$parent") || return 1
  [[ "$rp" == "$pp"/?* && "$pp" != / ]] || { c1_err "refusing to remove $path (outside $parent)"; return 1; }
  rm -rf -- "$rp"
}

c1_state_root() { printf '%s' "${C1_STATE_ROOT:-${XDG_RUNTIME_DIR:-/tmp}/c1-harness-$(id -u)}"; }

# c1_session_new ROOT -> prints session dir (mode 700) and writes lock
c1_session_new() {
  local root=$1 s
  umask 077
  mkdir -p "$root" && chmod 700 "$root"
  s=$(mktemp -d "$root/session.XXXXXXXX") || return 1
  mkdir -p "$s"/{tmp,reports,log}
  printf '%s %s %s\n' "$$" "$(c1_proc_start "$$")" "$s" >"$root/active"
  printf '%s' "$s"
}

# c1_proc_start PID -> kernel start time (guards against PID reuse)
c1_proc_start() { awk '{sub(/^.*\) /, ""); print $20}' "/proc/$1/stat" 2>/dev/null; }

# c1_pid_has_marker PID SESSION -> process env carries this session marker
c1_pid_has_marker() {
  local pid=$1 s=$2
  [[ -r "/proc/$pid/environ" ]] || return 1
  # No pipeline: under pipefail an early-exiting grep -q could SIGPIPE tr and hide a match.
  local envtext; envtext=$(tr '\0' '\n' 2>/dev/null <"/proc/$pid/environ") || return 1
  [[ $'\n'"$envtext"$'\n' == *$'\n'"C1_HARNESS_SESSION=$s"$'\n'* ]]
}

# c1_marked_pids SESSION -> pids (excluding the caller) carrying the marker
c1_marked_pids() {
  local s=$1 d pid
  for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    [[ "$pid" == "$$" || "$pid" == "${BASHPID:-$$}" ]] && continue
    if c1_pid_has_marker "$pid" "$s"; then printf '%s\n' "$pid"; fi
  done
  return 0
}

# c1_session_status ROOT -> none | active | stale
c1_session_status() {
  local root=$1 pid start s
  [[ -f "$root/active" ]] || { echo none; return; }
  read -r pid start s <"$root/active" || { echo stale; return; }
  if [[ -n "$pid" && -n "$start" && "$(c1_proc_start "$pid")" == "$start" ]]; then echo active; else echo stale; fi
}

c1_session_dir() { local _p _t s; read -r _p _t s <"$1/active" 2>/dev/null && printf '%s' "$s"; }

# c1_proc_stat_field PID N -> field N (1-based, counted AFTER "pid (comm)") of /proc/PID/stat
# Fields: 1=state 3=pgrp 4=session 20=starttime. comm may contain spaces/parens.
c1_proc_stat_field() { awk -v n="$2" '{sub(/^.*\) /, ""); print $n}' "/proc/$1/stat" 2>/dev/null; }

# c1_owned_pid PID SESSION [START] -> 0 only if PID is a live process carrying
# THIS session's marker and (when START is given) has that exact kernel start
# time. Rejects non-numeric, 0, 1 and the caller itself. The marker is the
# primary ownership proof (an unrelated reused PID carries no marker); the start
# time is a redundant defence-in-depth layer for recorded entries.
c1_owned_pid() {
  local pid=$1 s=$2 start=${3:-}
  [[ "$pid" =~ ^[0-9]+$ ]] && (( pid > 1 )) || return 1
  [[ "$pid" != "$$" && "$pid" != "${BASHPID:-$$}" ]] || return 1
  [[ -z "$start" || "$(c1_proc_start "$pid")" == "$start" ]] || return 1
  c1_pid_has_marker "$pid" "$s"
}

# c1_signal_owned SIG PID SESSION [START]
# Signals PID only if owned (above). Its PROCESS GROUP is signalled only when
# PID is also the leader of its own process group AND session (i.e. the setsid
# leader c1_spawn created): only that session's descendants can be members, so
# no unrelated process can be reached through the group signal.
c1_signal_owned() {
  local sig=$1 pid=$2 s=$3 start=${4:-}
  c1_owned_pid "$pid" "$s" "$start" || return 0
  if [[ "$(c1_proc_stat_field "$pid" 3)" == "$pid" && "$(c1_proc_stat_field "$pid" 4)" == "$pid" ]]; then
    kill "-$sig" -- "-$pid" 2>/dev/null || true
  fi
  kill "-$sig" "$pid" 2>/dev/null || true
}

# c1_cleanup_session ROOT SESSION  (idempotent)
# Every signal is ownership-verified: recorded entries ("pid starttime") must
# match marker AND start time (PID-reuse safe); marker-scan hits are signalled
# individually or, if they are their own setsid leader, as a group.
c1_cleanup_session() {
  local root=$1 s=$2 pid start
  [[ -n "$s" ]] || return 0
  # Never let cleanup's own helper processes carry the marker it hunts for.
  export -n C1_HARNESS_SESSION 2>/dev/null || true; unset C1_HARNESS_SESSION
  local -a rec=()
  if [[ -f "$s/pids" ]]; then mapfile -t rec <"$s/pids"; fi
  local sig
  for sig in TERM KILL; do
    local line
    for line in "${rec[@]}"; do
      read -r pid start <<<"$line"
      [[ -n "${start:-}" ]] || continue          # legacy/garbage line: never trusted
      c1_signal_owned "$sig" "$pid" "$s" "$start"
    done
    for pid in $(c1_marked_pids "$s"); do c1_signal_owned "$sig" "$pid" "$s"; done
    [[ $sig == KILL ]] && break
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      [[ -z "$(c1_marked_pids "$s")" ]] && break
      sleep 0.5
    done
  done
  if [[ -d "$s" ]]; then c1_safe_rm "$s" "$root" || return 1; fi
  if [[ -f "$root/active" ]]; then
    local as; as=$(c1_session_dir "$root" || true)
    if [[ "$as" == "$s" || ! -d "${as:-/nonexistent}" ]]; then rm -f -- "$root/active"; fi
  fi
  return 0
}

# c1_cleanup_all ROOT  remove every session directory under ROOT
c1_cleanup_all() {
  local root=$1 s
  [[ -d "$root" ]] || return 0
  for s in "$root"/session.*; do if [[ -d "$s" ]]; then c1_cleanup_session "$root" "$s"; fi; done
  rm -f -- "$root/active"
}

# c1_verify_cleanup SESSION -> 0 if no marked processes and no harness listeners
c1_verify_cleanup() {
  local s=$1 ok=0
  [[ -z "$(c1_marked_pids "$s")" ]] || { c1_err 'harness processes still running'; ok=1; }
  [[ ! -e "$s" ]] || { c1_err 'session directory still present'; ok=1; }
  c1_port_free "$C1_NOVNC_PORT" || { c1_err "port $C1_NOVNC_PORT still listening"; ok=1; }
  c1_port_free "$C1_APP_PORT" || { c1_err "port $C1_APP_PORT still listening"; ok=1; }
  return $ok
}

# ----------------------------------------------------------------- desktop --

c1_free_display() {
  local n
  for n in $(seq 99 140); do
    [[ ! -e "/tmp/.X11-unix/X$n" && ! -e "/tmp/.X$n-lock" ]] && { printf ':%s' "$n"; return 0; }
  done
  return 1
}

# Finite reads only: `tr </dev/urandom | head` dies of SIGPIPE under pipefail.
c1_gen_password() {
  local p=''
  while (( ${#p} < 8 )); do p+=$(head -c 64 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9'); done
  printf '%s' "${p:0:8}"
}

# Command builders are separate so the security flags can be regression-tested.
c1_cmd_xvfb()   { printf '%s\n' Xvfb "$1" -screen 0 1600x1000x24 -nolisten tcp -nolisten local -auth "$2/Xauthority"; }
c1_cmd_x11vnc() { printf '%s\n' x11vnc -display "$1" -auth "$2/Xauthority" -rfbport 0 -unixsock "$2/vnc.sock" \
                    -passwdfile "rm:$2/vncpass" -forever -noxdamage -nolookup -norc -nossl -quiet -o "$2/log/x11vnc.log"; }
c1_cmd_websockify() { printf '%s\n' websockify --web "$C1_NOVNC_WEB" --unix-target="$2/vnc.sock" "127.0.0.1:$1"; }

# c1_spawn SESSION NAME CMD...  start detached in own process group, record pid
c1_spawn() {
  local s=$1 name=$2; shift 2
  env C1_HARNESS_SESSION="$s" setsid "$@" >"$s/log/$name.log" 2>&1 </dev/null &
  local pid=$! st=''
  disown "$pid" 2>/dev/null || true   # no job-status noise when cleanup kills it
  for _ in 1 2 3 4 5 6 7 8 9 10; do st=$(c1_proc_start "$pid"); [[ -n "$st" ]] && break; sleep 0.02; done
  # A process that already exited records no start time and is never signalled by PID.
  if [[ -n "$st" ]]; then printf '%s %s\n' "$pid" "$st" >>"$s/pids"; fi
  printf '%s' "$pid"
}

# c1_start_display_vnc SESSION PASSWORD -> Xvfb + fluxbox + x11vnc (no TCP at all);
# exports DISPLAY/XAUTHORITY. The VNC server listens only on a 0600 Unix socket.
c1_start_display_vnc() {
  local s=$1 pass=$2 disp
  local -a cmd
  [[ "${C1_HARNESS_SESSION:-}" == "$s" ]] || { c1_err 'session marker not exported'; return 1; }
  disp=$(c1_free_display) || { c1_err 'no free X display'; return 1; }
  touch "$s/Xauthority" && chmod 600 "$s/Xauthority"
  xauth -q -f "$s/Xauthority" add "$disp" . "$(mcookie)" || return 1
  mapfile -t cmd < <(c1_cmd_xvfb "$disp" "$s")
  c1_spawn "$s" xvfb "${cmd[@]}" >/dev/null
  for _ in $(seq 1 50); do [[ -S "/tmp/.X11-unix/X${disp#:}" ]] && break; sleep 0.1; done
  [[ -S "/tmp/.X11-unix/X${disp#:}" ]] || { c1_err 'Xvfb failed to start'; return 1; }
  export DISPLAY=$disp XAUTHORITY="$s/Xauthority"
  c1_spawn "$s" fluxbox fluxbox >/dev/null
  ( umask 077; printf '%s\n' "$pass" >"$s/vncpass" )
  mapfile -t cmd < <(c1_cmd_x11vnc "$disp" "$s")
  c1_spawn "$s" x11vnc "${cmd[@]}" >/dev/null
  for _ in $(seq 1 50); do [[ -S "$s/vnc.sock" ]] && break; sleep 0.1; done
  [[ -S "$s/vnc.sock" ]] || { c1_err 'x11vnc failed to start'; return 1; }
  chmod 600 "$s/vnc.sock" 2>/dev/null || true
  for _ in $(seq 1 30); do [[ ! -e "$s/vncpass" ]] && break; sleep 0.1; done
  [[ ! -e "$s/vncpass" ]] || { c1_err 'x11vnc did not consume the one-time password file'; return 1; }
  # Nothing in the session may listen on TCP yet.
  # shellcheck disable=SC2046
  [[ -z "$(c1_listeners_for_pids $(c1_marked_pids "$s"))" ]] || { c1_err 'unexpected TCP listener before noVNC start'; return 1; }
}

# c1_start_websockify SESSION PORT -> noVNC/websockify on 127.0.0.1:PORT only; prints pid
c1_start_websockify() {
  local s=$1 port=$2 pid
  local -a cmd
  c1_port_free "$port" || { c1_err "port $port is already in use; run: c1-session.sh cleanup"; return 1; }
  mapfile -t cmd < <(c1_cmd_websockify "$port" "$s")
  pid=$(c1_spawn "$s" websockify "${cmd[@]}")
  for _ in $(seq 1 50); do c1_port_free "$port" || break; sleep 0.1; done
  c1_port_free "$port" && { c1_err 'noVNC/websockify failed to listen'; return 1; }
  c1_assert_desktop_listeners "$s" "$port" || return 1
  printf '%s' "$pid"
}

# c1_start_desktop SESSION PORT PASSWORD  (local/test helper: display+VNC+noVNC;
# the launcher instead verifies Codespaces privacy between the two steps)
c1_start_desktop() {
  local s=$1 port=$2 pass=$3
  c1_port_free "$port" || { c1_err "port $port is already in use; run: c1-session.sh cleanup"; return 1; }
  c1_start_display_vnc "$s" "$pass" || return 1
  c1_start_websockify "$s" "$port" >/dev/null
}

# Every harness TCP listener must be exactly the loopback noVNC port: Xvfb and
# x11vnc must expose none (X uses a Unix socket, VNC a 0600 Unix socket).
c1_assert_desktop_listeners() {
  local s=$1 port=$2 got
  # shellcheck disable=SC2046
  got=$(c1_listeners_for_pids $(c1_marked_pids "$s"))
  printf '%s\n' "$got" | c1_assert_loopback_only || return 1
  [[ "$got" == "127.0.0.1:$port" ]] || { c1_err "unexpected harness listener set: ${got//$'\n'/ }"; return 1; }
}

# ------------------------------------------------- Codespaces port privacy --
# Fail-closed technical verification (M-2). GitHub documents that a PRIVATE
# forwarded port is reachable only with GitHub authentication (browser sign-in,
# or `X-Github-Token: $GITHUB_TOKEN` for non-browser clients), while a PUBLIC
# port needs none. So: an unauthenticated probe must NOT receive a per-session
# nonce served by a decoy (no VNC behind it), and an authenticated probe MUST.
# Only then is the real noVNC started, and the check is repeated before the
# password is shown, before each supervised phase, and by a background monitor.
# There is no typed-confirmation fallback.

C1_PRIVACY_TIMEOUT=120
C1_NOVNC_SIGNATURE='noVNC'

# c1_codespace_base PORT -> https://NAME-PORT.DOMAIN (Codespaces variables only)
c1_codespace_base() {
  [[ "${CODESPACE_NAME:-}" =~ ^[A-Za-z0-9-]+$ ]] || return 1
  local dom=${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}
  [[ "$dom" =~ ^[a-z0-9.-]+$ ]] || return 1
  printf 'https://%s-%s.%s' "$CODESPACE_NAME" "$1" "$dom"
}

c1_codespace_url() { printf '%s/vnc.html?autoconnect=1&resize=remote' "$(c1_codespace_base "$1")"; }

# c1_write_header_file FILE NAME VALUE  (0600; keeps tokens out of argv/env of curl)
c1_write_header_file() { ( umask 077; printf '%s: %s\n' "$2" "$3" >"$1" ); }

# c1_https_probe URL OUTFILE [HEADERFILE] [CACERT] -> prints the HTTP status code
# (000 on transport failure). HTTPS only, no redirects followed, no proxy,
# minimal environment (no inherited CURL_CA_BUNDLE/HTTPS_PROXY).
c1_https_probe() {
  local url=$1 out=$2 hdr=${3:-} ca=${4:-}
  local -a a=(--silent --max-time 15 --proto '=https' --noproxy '*' -o "$out" -w '%{http_code}')
  if [[ -n "$hdr" ]]; then a+=(-H "@$hdr"); fi
  if [[ -n "$ca" ]]; then a+=(--cacert "$ca"); fi
  : >"$out"
  env -i PATH="$PATH" curl "${a[@]}" -- "$url" 2>/dev/null || true
}

# c1_privacy_check BASE PATH MARKER TOKENHDR [CACERT]
# 0 = verified private. Sets C1_PRIVACY_STATE to private | public | unverified.
c1_privacy_check() {
  local base=$1 path=$2 marker=$3 hdr=$4 ca=${5:-} f code
  C1_PRIVACY_STATE=unverified
  [[ -n "$base" && -n "$marker" && -s "$hdr" ]] || return 1
  f=$(mktemp "${TMPDIR:-/tmp}/c1probe.XXXXXXXX") || return 1
  code=$(c1_https_probe "$base$path" "$f" "" "$ca")
  if grep -qF -- "$marker" "$f"; then C1_PRIVACY_STATE=public; rm -f "$f"; return 1; fi
  # Any 2xx to an unauthenticated client is treated as exposure (fail closed).
  if [[ "$code" == 2?? ]]; then C1_PRIVACY_STATE=public; rm -f "$f"; return 1; fi
  code=$(c1_https_probe "$base$path" "$f" "$hdr" "$ca")
  if [[ "$code" == 200 ]] && grep -qF -- "$marker" "$f"; then C1_PRIVACY_STATE=private; rm -f "$f"; return 0; fi
  rm -f "$f"; return 1
}

c1_nonce() { od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }
c1_cmd_decoy() { printf '%s\n' python3 -I -m http.server --bind 127.0.0.1 --directory "$2/decoy" "$1"; }

# c1_verify_private_with_decoy SESSION PORT BASE TOKENHDR [CACERT]
# Serves a nonce on 127.0.0.1:PORT (no VNC behind it) until privacy is proven,
# then stops the decoy. Returns 1 (and stops the decoy) on any other outcome.
c1_verify_private_with_decoy() {
  local s=$1 port=$2 base=$3 hdr=$4 ca=${5:-} nonce pid ok=1 deadline
  local -a cmd
  c1_port_free "$port" || { c1_err "port $port already in use"; return 1; }
  nonce=$(c1_nonce); [[ ${#nonce} -eq 32 ]] || return 1
  mkdir -p "$s/decoy"
  printf 'C1 harness privacy decoy (no desktop behind this port)\n' >"$s/decoy/index.html"
  printf '%s\n' "$nonce" >"$s/decoy/c1-privacy-$nonce.txt"
  mapfile -t cmd < <(c1_cmd_decoy "$port" "$s")
  pid=$(c1_spawn "$s" decoy "${cmd[@]}")
  for _ in $(seq 1 50); do c1_port_free "$port" || break; sleep 0.1; done
  deadline=$(( SECONDS + C1_PRIVACY_TIMEOUT ))
  while (( SECONDS < deadline )); do
    if c1_privacy_check "$base" "/c1-privacy-$nonce.txt" "$nonce" "$hdr" "$ca"; then ok=0; break; fi
    if [[ $C1_PRIVACY_STATE == public ]]; then c1_err "port $port is reachable WITHOUT GitHub authentication (public); refusing to serve the desktop"; break; fi
    sleep 3
  done
  (( ok == 0 )) || [[ $C1_PRIVACY_STATE == public ]] || c1_err "could not prove port $port is private and forwarded (authenticated probe never reached the decoy)"
  local st; st=$(c1_proc_start "$pid")
  c1_signal_owned TERM "$pid" "$s" "$st"
  for _ in $(seq 1 50); do c1_port_free "$port" && break; sleep 0.1; done
  c1_signal_owned KILL "$pid" "$s" "$st"
  rm -rf -- "${s:?}/decoy"
  c1_port_free "$port" || { c1_err 'decoy did not stop'; return 1; }
  return $ok
}

# c1_verify_private_novnc BASE TOKENHDR [CACERT] -> privacy check against the real noVNC page
c1_verify_private_novnc() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    c1_privacy_check "$1" '/vnc.html' "$C1_NOVNC_SIGNATURE" "$2" "${3:-}" && return 0
    [[ $C1_PRIVACY_STATE == public ]] && return 1
    sleep 2
  done
  return 1
}

# c1_privacy_monitor_loop SESSION BASE TOKENHDR WSPID WSSTART [CACERT] [INTERVAL]
# Background, marker-scoped: tears noVNC down (and flags the session) as soon as
# the port is seen public, or after 3 consecutive unverifiable checks.
c1_privacy_monitor_loop() {
  local s=$1 base=$2 hdr=$3 ws=$4 wsst=$5 ca=${6:-} iv=${7:-15} misses=0
  trap 'exit 0' TERM
  while c1_owned_pid "$ws" "$s" "$wsst"; do
    sleep "$iv"
    if c1_privacy_check "$base" '/vnc.html' "$C1_NOVNC_SIGNATURE" "$hdr" "$ca"; then misses=0; continue; fi
    if [[ $C1_PRIVACY_STATE == public ]]; then misses=99; else misses=$((misses + 1)); fi
    if (( misses >= 3 )); then
      printf '%s %s\n' "$(c1_utc)" "$C1_PRIVACY_STATE" >"$s/privacy-failed"
      c1_signal_owned TERM "$ws" "$s" "$wsst"; sleep 1; c1_signal_owned KILL "$ws" "$s" "$wsst"
      return 0
    fi
  done
}

# c1_repo_owner_type OWNER/REPO AUTHHDR [CACERT] [API_BASE] -> GitHub owner type (User | Organization)
# (CACERT/API_BASE exist for the offline regression tests; the launcher passes neither.)
c1_repo_owner_type() {
  local repo=$1 hdr=$2 ca=${3:-} api=${4:-https://api.github.com} f code
  [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ && -s "$hdr" ]] || return 1
  f=$(mktemp "${TMPDIR:-/tmp}/c1api.XXXXXXXX") || return 1
  code=$(c1_https_probe "$api/repos/$repo" "$f" "$hdr" "$ca")
  if [[ "$code" == 200 ]]; then
    node -e 'const j=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));process.stdout.write(String((j.owner&&j.owner.type)||""))' "$f" 2>/dev/null || true
  fi
  rm -f "$f"
}

# c1_gh_visibility PORT -> visibility via gh (requires codespace scope), or nothing
c1_gh_visibility() {
  [[ -n "${CODESPACE_NAME:-}" ]] && command -v gh >/dev/null 2>&1 || return 1
  local out; out=$(gh codespace ports -c "$CODESPACE_NAME" --json sourcePort,visibility 2>/dev/null) || return 1
  node -e 'const p=JSON.parse(process.argv[1]).find(x=>x.sourcePort===Number(process.argv[2]));if(p)process.stdout.write(String(p.visibility).toLowerCase())' "$out" "$1" 2>/dev/null
}

# --------------------------------------------------------------- watchdog --
# L-3: a marker-scoped watchdog in its own session tears the harness session
# down if the launcher disappears (SIGKILL, OOM, closed pty during cleanup).
c1_watchdog_loop() {
  local root=$1 s=$2 lpid=$3 lstart=$4
  trap 'exit 0' TERM
  # A SIGKILLed launcher can linger as an unreaped zombie with the same start
  # time; zombie (Z) or dead (X) counts as gone.
  while [[ "$(c1_proc_start "$lpid")" == "$lstart" && ! "$(c1_proc_stat_field "$lpid" 1)" =~ ^[ZX]$ ]]; do sleep 1; done
  c1_cleanup_session "$root" "$s"
}

# c1_start_watchdog ROOT SESSION LAUNCHER_PID LIBPATH
c1_start_watchdog() {
  local root=$1 s=$2 lpid=$3 lib=$4 lst
  lst=$(c1_proc_start "$lpid"); [[ -n "$lst" ]] || return 1
  # shellcheck disable=SC2016
  c1_spawn "$s" watchdog bash -c 'source "$1"; c1_watchdog_loop "$2" "$3" "$4" "$5"' _ "$lib" "$root" "$s" "$lpid" "$lst" >/dev/null
}

# ------------------------------------------------------- environment (L-4) --
# Test phases run under `env -i` with this allowlist only (no PLAYWRIGHT_*,
# NODE_OPTIONS/NODE_PATH, npm_config_*, proxies or CA overrides are inherited).
C1_ENV_ALLOW=(HOME PATH LANG LC_ALL TERM USER DISPLAY XAUTHORITY C1_HARNESS_SESSION)
c1_clean_env() {
  local v
  printf '%s\0' env -i
  for v in "${C1_ENV_ALLOW[@]}"; do
    if [[ -n "${!v+x}" ]]; then printf '%s\0' "$v=${!v}"; fi
  done
}

# c1_expected_browser FRONTEND NAME -> browserVersion from playwright-core/browsers.json
c1_expected_browser() {
  node -e 'const b=require(process.argv[1]+"/node_modules/playwright-core/browsers.json").browsers.find(x=>x.name===process.argv[2]);if(b)process.stdout.write(b.browserVersion)' "$1" "$2" 2>/dev/null
}

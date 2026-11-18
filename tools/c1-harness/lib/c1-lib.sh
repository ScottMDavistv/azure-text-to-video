# shellcheck shell=bash
# shellcheck disable=SC2034  # constants are consumed by c1-session.sh and tests
# C1 validation harness library. Development-only. Sourced by c1-session.sh
# and by the regression tests. Functions take explicit arguments so tests can
# exercise them against fixture repositories; the launcher alone supplies the
# pinned approved values below. There is deliberately NO environment override
# for the approved SHAs.

# Baseline is the protected-main snapshot containing the independently reviewed
# PR #46 auth implementation; new PR #60 test and UI changes are explicitly
# enumerated and require their own independent review before LIVE use.
C1_AUTH_SHA_APPROVED='81c352b2c6f7fdae4e94f2ff4e98a7e1c2bdf6c5'
C1_PW_SHA_APPROVED='e1873111e2304a7a739e0f35e2ecdca1d43de44d'
C1_PLAYWRIGHT_VERSION='1.65.1'
# I-5: the Chromium build Playwright 1.65.1 pins (browsers.json rev 1255, both
# chromium and chromium-headless-shell). R10 requires this literal value too.
C1_CHROMIUM_VERSION='159.0.8154.6'
# Exact verified diff from the main baseline to the tested PR #60 head. Anything
# else (e.g. src/entraAuth.ts) => reject. This list must equal
# `git diff --no-renames --name-only AUTH PW`; it is never widened to pass.
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
# Production (non-test) files inside C1_AUTH_TO_PW_FILES whose AUTH -> PW change
# has NOT been independently security-certified. Each is recorded in the
# evidence with its exact blob SHA at PW, and row R13 stays INCOMPLETE (so the
# session can never be READY) until a separate review certifies it and this
# list is emptied in a reviewed commit. frontend/src/App.tsx: e187311 adds the
# "Signed out locally. Microsoft SSO session may remain active." status.
C1_PROD_REVIEW_PENDING='frontend/src/App.tsx'
# Allowlisted paths that are test/CI infrastructure, not production runtime code.
# Any other allowlisted path must appear in C1_PROD_REVIEW_PENDING.
C1_TEST_INFRA_RE='^(frontend/e2e/|frontend/\.gitignore$|frontend/playwright\.config\.mjs$|frontend/src/c1BuildInfo\.ts$|\.github/workflows/frontend-quality-gates\.yml$)'
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

# c1_verify_prod_pending REPO AUTH PW ALLOWED PENDING -> every allowlisted
# non-test path is listed as pending review, and every pending path really is
# an allowlisted file that differs between AUTH and PW. Prints one
# "path@blobsha" per pending file (blob at PW), or "none".
c1_verify_prod_pending() {
  local repo=$1 auth=$2 pw=$3 allowed=$4 pending=$5 f
  local -a recs=()
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ "$f" =~ $C1_TEST_INFRA_RE ]] && continue
    grep -qxF -- "$f" <<<"$pending" || { c1_err "production file $f is allowlisted but not marked pending independent review"; return 1; }
  done <<<"$allowed"
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    grep -qxF -- "$f" <<<"$allowed" || { c1_err "pending-review file $f is not in the approved diff"; return 1; }
    git -C "$repo" diff --quiet "$auth" "$pw" -- "$f" && { c1_err "pending-review file $f does not differ between AUTH and PW"; return 1; }
    recs+=("$f@$(git -C "$repo" rev-parse "$pw:$f")") || return 1
  done <<<"$pending"
  if (( ${#recs[@]} )); then (IFS=,; printf '%s\n' "${recs[*]}"); else echo none; fi
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

# ------------------------------------------------ listener evidence (M-3) --
# Every TCP-listener decision is FAIL-CLOSED and drawn from two independent
# sources that must agree: the kernel tables /proc/net/tcp and /proc/net/tcp6
# (IPv4 and IPv6) and `ss -Hltne`. Ownership is proved by socket inode
# (/proc/PID/fd -> socket:[INODE]), never by parsing ss's users: text. Missing,
# unreadable, malformed, empty-but-inconsistent or contradictory evidence is a
# FAILURE (return 1 / 2), never "no listener".
# Constants (not environment-overridable; the regression tests reassign them
# after sourcing to feed fixture kernel tables and a fake ss through the REAL
# parsers).
C1_PROC_NET=/proc/net
C1_SS_BIN=ss

# c1_v6_canon TEXT -> RFC 5952 lowercase form of an IPv6 address (no brackets).
# Accepts "::" compression and an embedded dotted IPv4 tail. rc 1 if malformed.
# The _v variants assign C1_CANON instead of printing (no subshell: these run in
# the bounded teardown loops through c1_port_free).
c1_v6_canon() { _c1_v6_canon_v "$1" || return 1; printf '%s' "$C1_CANON"; }
_c1_v6_canon_v() {
  C1_CANON=''
  local a=${1,,} head tail g i best=-1 bestlen=0 run=-1 runlen=0 res=''
  local -a hg=() tg=() all=()
  [[ "$a" =~ ^[0-9a-f:.]{2,45}$ ]] || return 1
  if [[ "$a" =~ ^(.*:)([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
    for i in 2 3 4 5; do (( 10#${BASH_REMATCH[i]} <= 255 )) || return 1; done
    printf -v g '%x:%x' $(( 10#${BASH_REMATCH[2]} * 256 + 10#${BASH_REMATCH[3]} )) $(( 10#${BASH_REMATCH[4]} * 256 + 10#${BASH_REMATCH[5]} ))
    a=${BASH_REMATCH[1]}$g
  fi
  if [[ "$a" == *::* ]]; then
    head=${a%%::*} tail=${a#*::}
    [[ "$tail" != *::* ]] || return 1
    if [[ -n "$head" ]]; then IFS=: read -ra hg <<<"$head"; fi
    if [[ -n "$tail" ]]; then IFS=: read -ra tg <<<"$tail"; fi
    (( ${#hg[@]} + ${#tg[@]} <= 7 )) || return 1
    all=("${hg[@]}")
    for (( i = ${#hg[@]} + ${#tg[@]}; i < 8; i++ )); do all+=(0); done
    all+=("${tg[@]}")
  else
    IFS=: read -ra all <<<"$a"
    [[ "$a" != *: ]] || return 1
  fi
  (( ${#all[@]} == 8 )) || return 1
  for (( i = 0; i < 8; i++ )); do
    g=${all[i]}
    [[ "$g" =~ ^[0-9a-f]{1,4}$ ]] || return 1
    all[i]=$(( 16#$g ))
  done
  for (( i = 0; i < 8; i++ )); do       # longest run (>= 2) of zero groups, first wins
    if (( all[i] == 0 )); then
      (( run < 0 )) && { run=$i; runlen=0; }
      runlen=$(( runlen + 1 ))
      if (( runlen > bestlen )); then best=$run; bestlen=$runlen; fi
    else run=-1; fi
  done
  (( bestlen >= 2 )) || best=-1
  for (( i = 0; i < 8; i++ )); do
    if (( i == best )); then res+='::'; i=$(( best + bestlen - 1 )); continue; fi
    [[ -z "$res" || "$res" == *: ]] || res+=':'
    printf -v g '%x' "${all[i]}"; res+=$g
  done
  C1_CANON=$res
}

# c1_canon_hostport TEXT -> canonical "a.b.c.d:PORT" or "[v6]:PORT" (ss syntax:
# "*:P" is the dual-stack wildcard [::]; a "%iface" scope suffix is dropped).
c1_canon_hostport() { _c1_canon_hostport_v "$1" || return 1; printf '%s' "$C1_CANON"; }
_c1_canon_hostport_v() {
  local t=$1 h p v6
  if [[ "$t" =~ ^\*:([0-9]{1,5})$ ]]; then h='::'; p=${BASH_REMATCH[1]}; v6=1
  elif [[ "$t" =~ ^\[([0-9A-Fa-f:.]+)(%[A-Za-z0-9_.@-]+)?\](%[A-Za-z0-9_.@-]+)?:([0-9]{1,5})$ ]]; then h=${BASH_REMATCH[1]}; p=${BASH_REMATCH[4]}; v6=1
  elif [[ "$t" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})(%[A-Za-z0-9_.@-]+)?:([0-9]{1,5})$ ]]; then
    local i; for i in 1 2 3 4; do (( 10#${BASH_REMATCH[i]} <= 255 )) || return 1; done
    h=$(( 10#${BASH_REMATCH[1]} )).$(( 10#${BASH_REMATCH[2]} )).$(( 10#${BASH_REMATCH[3]} )).$(( 10#${BASH_REMATCH[4]} )); p=${BASH_REMATCH[6]}; v6=0
  else return 1; fi
  (( 10#$p <= 65535 )) || return 1
  if (( v6 )); then _c1_v6_canon_v "$h" || return 1; printf -v C1_CANON '[%s]:%d' "$C1_CANON" $(( 10#$p ))
  else printf -v C1_CANON '%s:%d' "$h" $(( 10#$p )); fi
}

# c1_proc_hex_addr FAMILY HEXADDR:HEXPORT -> canonical host:port. The kernel
# prints each 32-bit address word in host (little-endian) byte order.
c1_proc_hex_addr() { _c1_proc_hex_addr_v "$1" "$2" || return 1; printf '%s' "$C1_CANON"; }
_c1_proc_hex_addr_v() {
  local fam=$1 x=$2 a p w i res=''
  if [[ $fam == 4 ]]; then
    [[ "$x" =~ ^([0-9A-Fa-f]{8}):([0-9A-Fa-f]{4})$ ]] || return 1
    a=${BASH_REMATCH[1]} p=${BASH_REMATCH[2]}
    printf -v C1_CANON '%d.%d.%d.%d:%d' $(( 16#${a:6:2} )) $(( 16#${a:4:2} )) $(( 16#${a:2:2} )) $(( 16#${a:0:2} )) $(( 16#$p ))
  else
    [[ "$x" =~ ^([0-9A-Fa-f]{32}):([0-9A-Fa-f]{4})$ ]] || return 1
    a=${BASH_REMATCH[1]} p=${BASH_REMATCH[2]}
    for i in 0 8 16 24; do
      w=${a:i:8}
      res+=${w:6:2}${w:4:2}:${w:2:2}${w:0:2}:
    done
    _c1_v6_canon_v "${res%:}" || return 1
    printf -v C1_CANON '[%s]:%d' "$C1_CANON" $(( 16#$p ))
  fi
}

# c1_parse_proc_net_listen FAMILY FILE -> "INODE host:port" per TCP LISTEN row.
# rc 1 on an unreadable/empty file, a missing or wrong header, or ANY malformed row.
c1_parse_proc_net_listen() {
  local fam=$1 file=$2 hdr sl la ra st _q _tr _rt _uid _to ino _rest addr
  [[ -r "$file" && -f "$file" ]] || { c1_err "listener evidence: cannot read $file"; return 1; }
  {
    IFS= read -r hdr || { c1_err "listener evidence: $file is empty"; return 1; }
    [[ "$hdr" =~ ^[[:space:]]*sl[[:space:]]+local_address[[:space:]]+(rem_address|remote_address)[[:space:]]+st[[:space:]] && "$hdr" == *inode* ]] \
      || { c1_err "listener evidence: unexpected header in $file"; return 1; }
    while read -r sl la ra st _q _tr _rt _uid _to ino _rest; do
      [[ -z "$sl" ]] && continue
      [[ "$sl" =~ ^[0-9]+:$ && "$st" =~ ^[0-9A-Fa-f]{2}$ && "$ino" =~ ^[0-9]+$ ]] \
        || { c1_err "listener evidence: malformed row in $file"; return 1; }
      _c1_proc_hex_addr_v "$fam" "$ra" || { c1_err "listener evidence: malformed address in $file"; return 1; }
      _c1_proc_hex_addr_v "$fam" "$la" || { c1_err "listener evidence: malformed address in $file"; return 1; }
      addr=$C1_CANON
      [[ "${st^^}" == 0A ]] || continue
      (( ino > 0 )) || { c1_err "listener evidence: LISTEN socket $addr without an inode in $file"; return 1; }
      printf '%s %s\n' "$ino" "$addr"
    done
  } <"$file"
}

# c1_ipv6_unavailable -> 0 only if this network namespace demonstrably has no
# IPv6: no tcp6 table, no if_inet6, and the kernel refuses AF_INET6 sockets.
c1_ipv6_unavailable() {
  [[ ! -e "$C1_PROC_NET/tcp6" && ! -e "$C1_PROC_NET/if_inet6" ]] || return 1
  ! python3 -I -c 'import socket; socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()' 2>/dev/null
}

# c1_proc_listen_table -> "INODE host:port" for every IPv4 AND IPv6 TCP listener.
c1_proc_listen_table() {
  local t4 t6=''
  t4=$(c1_parse_proc_net_listen 4 "$C1_PROC_NET/tcp") || return 1
  if [[ -e "$C1_PROC_NET/tcp6" ]]; then t6=$(c1_parse_proc_net_listen 6 "$C1_PROC_NET/tcp6") || return 1
  elif ! c1_ipv6_unavailable; then c1_err 'listener evidence: IPv6 is available but no IPv6 TCP table could be read'; return 1
  fi
  printf '%s\n' "$t4" "$t6" | sed '/^$/d'
}

# c1_parse_ss_listen -> "INODE host:port" from `ss -Hltne` text on stdin.
# rc 1 on any line that is not a well-formed LISTEN row with exactly one ino:N.
c1_parse_ss_listen() {
  local line st q1 q2 la pa rest tok ino addr
  while IFS= read -r line; do
    [[ -n "${line//[[:space:]]/}" ]] || continue
    read -r st q1 q2 la pa rest <<<"$line"
    [[ "$st" == LISTEN && "$q1" =~ ^[0-9]+$ && "$q2" =~ ^[0-9]+$ && -n "$pa" ]] \
      || { c1_err 'listener evidence: malformed ss row'; return 1; }
    ino=''
    for tok in $rest; do
      if [[ "$tok" =~ ^ino:([0-9]+)$ ]]; then
        [[ -z "$ino" ]] || { c1_err 'listener evidence: ss row with several inodes'; return 1; }
        ino=${BASH_REMATCH[1]}
      fi
    done
    [[ -n "$ino" ]] && (( ino > 0 )) || { c1_err 'listener evidence: ss row without a socket inode'; return 1; }
    _c1_canon_hostport_v "$la" || { c1_err 'listener evidence: ss row with a malformed local address'; return 1; }
    addr=$C1_CANON
    printf '%s %s\n' "$ino" "$addr"
  done
}

# c1_ss_listen_table -> parsed `ss -Hltne`; rc 1 if ss is missing, fails or is malformed.
c1_ss_listen_table() {
  local out
  command -v "$C1_SS_BIN" >/dev/null 2>&1 || { c1_err 'listener evidence: ss (iproute2) is unavailable'; return 1; }
  out=$("$C1_SS_BIN" -Hltne 2>/dev/null) || { c1_err 'listener evidence: ss failed'; return 1; }
  c1_parse_ss_listen <<<"$out"
}

# c1_listen_tables -> sets C1_LT_PROC / C1_LT_SS (both validated, cross-checked
# for blindness: one source empty while the other is not is a failure).
c1_listen_tables() {
  C1_LT_PROC='' C1_LT_SS=''
  C1_LT_PROC=$(c1_proc_listen_table) || return 1
  C1_LT_SS=$(c1_ss_listen_table) || return 1
  if { [[ -z "$C1_LT_PROC" ]] && [[ -n "$C1_LT_SS" ]]; } || { [[ -n "$C1_LT_PROC" ]] && [[ -z "$C1_LT_SS" ]]; }; then
    c1_err 'listener evidence: kernel tables and ss disagree (one reports no listeners at all)'; return 1
  fi
}

# c1_port_free PORT -> 0 only if BOTH sources prove nothing listens on PORT on
# any IPv4 or IPv6 address; 1 if something listens; 2 if that cannot be proven.
# (Callers treat any non-zero as "not free", so missing evidence fails closed.)
c1_port_free() {
  local port=$1 ino addr hit=0
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || return 2
  c1_listen_tables 2>/dev/null || return 2
  while read -r ino addr; do
    [[ -n "$ino" ]] || continue
    [[ "${addr##*:}" == "$((10#$port))" ]] && hit=1
  done <<<"$C1_LT_PROC"$'\n'"$C1_LT_SS"
  (( hit == 0 )) || return 1
}

# c1_self_chain -> this (sub)shell and its ancestors up to and including the
# top-level script $$ (excluded from orphan reports only; never from listener scans).
c1_self_chain() {
  local p=${BASHPID:-$$} n=0
  while [[ "$p" =~ ^[0-9]+$ ]] && (( p > 1 && n < 64 )); do
    printf '%s\n' "$p"; n=$((n + 1))
    [[ "$p" == "$$" ]] && break
    p=$(awk '{sub(/^.*\) /, ""); print $2}' "/proc/$p/stat" 2>/dev/null) || break
  done
}

# c1_proc_tree -> "PID PPID SID" for every readable process (comm may contain spaces/parens).
c1_proc_tree() {
  local d f
  for d in /proc/[0-9]*; do
    f=$(awk '{sub(/^.*\) /, ""); print $2, $4}' "$d/stat" 2>/dev/null) || continue
    [[ -n "$f" ]] && printf '%s %s\n' "${d#/proc/}" "$f"
  done
}

# c1_tree_attributed PID -> 0 if PID is a live recorded leader, in a leader's
# setsid session, or descends from a leader (needs C1_TREE_PPID/SID, C1_TREE_LEAD).
c1_tree_attributed() {
  local p=$1 n=0
  [[ -n "${C1_TREE_LEAD[$p]:-}" || -n "${C1_TREE_LEAD[${C1_TREE_SID[$p]:-x}]:-}" ]] && return 0
  while (( n++ < 64 )); do
    p=${C1_TREE_PPID[$p]:-}
    [[ "$p" =~ ^[0-9]+$ ]] && (( p > 1 )) || return 1
    [[ -n "${C1_TREE_LEAD[$p]:-}" ]] && return 0
  done
  return 1
}

# c1_load_tree SESSION -> fills C1_TREE_* (strict=1: a malformed record or a
# live recorded process whose marker cannot be read is an error, rc 1).
c1_load_tree() {
  local s=$1 strict=${2:-0} pid st extra pp sd
  declare -gA C1_TREE_LEAD=() C1_TREE_PPID=() C1_TREE_SID=()
  if [[ -e "$s/pids" ]]; then
    [[ -r "$s/pids" ]] || { (( ! strict )) || { c1_err 'listener evidence: harness pid record unreadable'; return 1; }; }
    while read -r pid st extra; do
      [[ -n "$pid" ]] || continue
      if ! [[ "$pid" =~ ^[0-9]+$ && "$st" =~ ^[0-9]+$ && -z "$extra" ]]; then
        (( ! strict )) || { c1_err 'listener evidence: malformed harness pid record'; return 1; }; continue
      fi
      [[ "$(c1_proc_start "$pid")" == "$st" ]] || continue            # exited
      [[ "$(c1_proc_stat_field "$pid" 1)" =~ ^[ZX]$ ]] && continue     # zombie holds no sockets
      if ! c1_pid_has_marker "$pid" "$s"; then
        [[ "$(c1_proc_start "$pid")" == "$st" ]] || continue          # exited meanwhile
        (( ! strict )) || { c1_err "listener evidence: recorded harness process $pid is running but its session marker cannot be verified"; return 1; }
        continue
      fi
      C1_TREE_LEAD[$pid]=1
    done <"$s/pids"
  fi
  while read -r pid pp sd; do C1_TREE_PPID[$pid]=$pp; C1_TREE_SID[$pid]=$sd; done < <(c1_proc_tree)
}

# c1_harness_pids SESSION -> every process attributable to the session: marker
# carriers, live recorded c1_spawn leaders, members of their setsid sessions and
# their descendants (a child that dropped the marker with env -i, or that called
# setsid itself, as fluxbox does for fbsetbg, stays attributable).
# rc 1 if the record is malformed or a live recorded process cannot be verified.
c1_harness_pids() {
  local s=$1 pid
  local -A attrib=()
  c1_load_tree "$s" 1 || return 1
  for pid in "${!C1_TREE_PPID[@]}"; do
    if c1_tree_attributed "$pid" || c1_pid_has_marker "$pid" "$s"; then attrib[$pid]=1; fi
  done
  (( ${#attrib[@]} )) && printf '%s\n' "${!attrib[@]}" | sort -n
  return 0
}

# c1_pid_socket_inodes PID START -> socket inodes held by PID. rc 1 if PID is
# still the same live process but its fd table cannot be read (fail closed).
c1_pid_socket_inodes() {
  local pid=$1 st=$2 f l
  local -a inos=()
  if [[ ! -r "/proc/$pid/fd" || ! -x "/proc/$pid/fd" ]]; then
    [[ "$(c1_proc_start "$pid")" == "$st" && ! "$(c1_proc_stat_field "$pid" 1)" =~ ^[ZX]$ ]] || return 0
    c1_err "listener evidence: cannot read the fd table of harness process $pid"; return 1
  fi
  for f in "/proc/$pid/fd/"*; do
    l=$(readlink "$f" 2>/dev/null) || continue          # fd closed meanwhile
    [[ "$l" =~ ^socket:\[([0-9]+)\]$ ]] && inos+=("${BASH_REMATCH[1]}")
  done
  # PID reused while scanning: the original process is gone (holds nothing).
  [[ "$(c1_proc_start "$pid")" == "$st" ]] || return 0
  (( ${#inos[@]} )) && printf '%s\n' "${inos[@]}"
  return 0
}

c1_safe_comm() { local c; c=$(head -c 64 "/proc/$1/comm" 2>/dev/null | tr -cd 'A-Za-z0-9._-'); printf '%s' "${c:0:16}"; }

# c1_harness_listeners SESSION -> "host:port pid=N comm=C ino=I" per harness-
# owned TCP listener (IPv4 and IPv6), sorted. rc 1 = evidence unavailable or
# inconsistent: callers MUST fail closed; nothing is printed on rc 1.
# Observed inodes are recorded in SESSION/listen-inodes for cleanup verification (L-3).
c1_harness_listeners() {
  local s=$1 pid st ino addr pidlist inos comm
  local -A own=() kt=() sst=()
  local -a rows=()
  pidlist=$(c1_harness_pids "$s") || return 1
  for pid in $pidlist; do
    st=$(c1_proc_start "$pid"); [[ -n "$st" ]] || continue
    inos=$(c1_pid_socket_inodes "$pid" "$st") || return 1
    comm=$(c1_safe_comm "$pid")
    for ino in $inos; do own[$ino]+="$pid:${comm:-unknown} "; done
  done
  c1_listen_tables || return 1
  while read -r ino addr; do [[ -n "$ino" ]] && kt[$ino]=$addr; done <<<"$C1_LT_PROC"
  while read -r ino addr; do [[ -n "$ino" ]] && sst[$ino]=$addr; done <<<"$C1_LT_SS"
  for ino in "${!own[@]}"; do
    if [[ -n "${kt[$ino]:-}" ]]; then
      [[ "${sst[$ino]:-}" == "${kt[$ino]}" ]] \
        || { c1_err "listener evidence: harness socket $ino is listening on ${kt[$ino]} in the kernel table but ss reports '${sst[$ino]:-nothing}'"; return 1; }
      for pid in ${own[$ino]}; do rows+=("${kt[$ino]} pid=${pid%%:*} comm=${pid#*:} ino=$ino"); done
      if ! printf '%s\n' "$ino" >>"$s/listen-inodes" 2>/dev/null; then
        c1_err 'listener evidence: cannot persist observed listener inode; failing closed'
        return 1
      fi
    elif [[ -n "${sst[$ino]:-}" ]]; then
      c1_err "listener evidence: ss reports harness socket $ino listening on ${sst[$ino]} but the kernel tables do not"; return 1
    fi
  done
  (( ${#rows[@]} )) && printf '%s\n' "${rows[@]}" | LC_ALL=C sort -u
  return 0
}

# c1_harness_orphans SESSION -> "pid=N comm=C" for marked processes that are
# neither this shell's chain nor attributable to a live recorded c1_spawn leader
# (e.g. a Vite or browser left over from an earlier phase; L-2). Report only.
c1_harness_orphans() {
  local s=$1 pid
  local -A self=()
  for pid in $(c1_self_chain); do self[$pid]=1; done
  c1_load_tree "$s" 0 || return 0
  local p n
  for pid in "${!C1_TREE_PPID[@]}"; do
    [[ -n "${self[$pid]:-}" ]] && continue
    c1_tree_attributed "$pid" && continue
    p=$pid n=0                                   # live descendants of this script (its own subshells, a running phase)
    while (( n++ < 64 )) && [[ "${C1_TREE_PPID[$p]:-}" =~ ^[0-9]+$ ]] && (( ${C1_TREE_PPID[$p]} > 1 )); do
      p=${C1_TREE_PPID[$p]}; [[ -n "${self[$p]:-}" ]] && continue 2
    done
    c1_pid_has_marker "$pid" "$s" || continue
    printf 'pid=%s comm=%s\n' "$pid" "$(c1_safe_comm "$pid")"
  done | sort -t= -k2 -n
}

# c1_listener_state SESSION STATE DETAIL -> records the listener verdict for the
# evidence (STATE: verified | failed-unexpected | failed-unavailable). The detail
# is sanitized: addresses, pids, comm names and inodes only (no environ/argv).
c1_listener_state() {
  local d=${3//$'\n'/; }
  d=$(printf '%s' "$d" | tr -cd 'A-Za-z0-9.:;=, _[]-'); d=${d:0:200}
  printf '%s\n%s\n' "$2" "$d" >"$1/listener-state" 2>/dev/null || true
}

# c1_assert_loopback_only "host:port" lines on stdin (canonical forms, or the
# rows of c1_harness_listeners). IPv4 127/8 and IPv6 ::1 only; wildcards
# (0.0.0.0, [::], *), external and IPv4-mapped addresses are all rejected.
c1_assert_loopback_only() {
  local l a bad=0
  while read -r l _; do
    [[ -z "$l" ]] && continue
    _c1_canon_hostport_v "$l" || { c1_err "unparseable listener address: $l"; bad=1; continue; }
    a=$C1_CANON
    case "$a" in
      127.*:*|'[::1]':*) ;;
      *) c1_err "non-loopback listener detected: $a"; bad=1 ;;
    esac
  done
  (( bad == 0 ))
}

# c1_installer_env -> NUL-separated `env -u ...` prefix for dependency installers
# (I-3). Removes GitHub/Codespaces credentials and any token/secret-like
# variable, plus variables that redirect or alter npm, Node, Vite or Playwright
# (and NODE_TLS_REJECT_UNAUTHORIZED, which disables TLS verification).
# Preserved as required installation settings: PATH, HOME, locale, proxy /
# no-proxy settings and the CA-trust variables a TLS-intercepting proxy needs
# (NODE_EXTRA_CA_CERTS, SSL_CERT_FILE, SSL_CERT_DIR). Package content is
# protected independently by the lockfile's sha512 integrity and by R10.
c1_installer_env() {
  local v
  printf '%s\0' env
  for v in $(compgen -e); do
    case $v in
      HTTP_PROXY|HTTPS_PROXY|NO_PROXY|http_proxy|https_proxy|no_proxy|NODE_EXTRA_CA_CERTS|SSL_CERT_FILE|SSL_CERT_DIR) ;;
      GITHUB_*|GH_*|*TOKEN*|*token*|*SECRET*|*secret*|*PASSWORD*|*password*|*_PAT|*CREDENTIAL*|\
      CODESPACE_*|ACTIONS_*|npm_config_*|NPM_CONFIG_*|PLAYWRIGHT_*|PW_*|NODE_OPTIONS|NODE_PATH|\
      NODE_TLS_REJECT_UNAUTHORIZED|VITE_*|__VITE_*)
        printf '%s\0' -u "$v" ;;
    esac
  done
}

# c1_ienv CMD...  run CMD under the installer environment
c1_ienv() { local -a e; mapfile -d '' -t e < <(c1_installer_env); "${e[@]}" "$@"; }

# c1_check_vite_host_guard WORKTREE (L-11) -> the approved Vite dev server on 5173
# keeps Vite's Host-header check: exactly one vite config, bound to 127.0.0.1,
# no allowedHosts anywhere in the approved frontend config/launch path, and the
# approved Playwright config starts it with `--host 127.0.0.1`. A forwarded 5173
# request carries Host: <codespace>-5173.<domain>, which that check rejects.
c1_check_vite_host_guard() {
  local fe=$1/frontend f cfg='' n=0
  for f in "$fe"/vite.config.{ts,mts,cts,js,mjs,cjs}; do [[ -e "$f" ]] && { n=$((n + 1)); cfg=$f; }; done
  (( n == 1 )) || { c1_err "expected exactly one frontend/vite.config.* (found $n)"; return 1; }
  if grep -Eq 'allowedHosts|allowed-hosts|__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS' -- "$cfg" "$fe/package.json" "$fe/playwright.config.mjs"; then
    c1_err 'approved Vite/Playwright configuration sets allowedHosts: the 5173 Host-header check would be disabled'; return 1
  fi
  grep -Eq "host:[[:space:]]*'127\.0\.0\.1'" -- "$cfg" || { c1_err 'approved Vite dev server is not bound to 127.0.0.1'; return 1; }
  grep -Fq "command: 'npm run dev -- --host 127.0.0.1'" -- "$fe/playwright.config.mjs" \
    || { c1_err 'approved Playwright webServer does not start Vite with --host 127.0.0.1'; return 1; }
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
  { c1_ienv $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $missing \
    || { c1_ienv $sudo apt-get update -qq && c1_ienv $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $missing; }; } >/dev/null
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
    (cd "$fe" && c1_ienv npm ci --no-audit --no-fund --loglevel=error) || return 1
    printf '%s' "$lockhash" >"$marker"
  else
    c1_log 'frontend dependencies match approved lockfile'
  fi
  ver=$(node -p "require('$fe/node_modules/@playwright/test/package.json').version" 2>/dev/null || true)
  if [[ "$ver" != "$C1_PLAYWRIGHT_VERSION" ]]; then
    c1_log "installing @playwright/test@$C1_PLAYWRIGHT_VERSION (no-save, ignore-scripts, as approved README)"
    (cd "$fe" && c1_ienv npm install --no-save --ignore-scripts --no-audit --no-fund --loglevel=error "@playwright/test@$C1_PLAYWRIGHT_VERSION") || return 1
  fi
  (cd "$fe" && c1_ienv npx --no-install playwright install chromium) || return 1
  if [[ ! -f "$cache/deps-ok-$C1_PLAYWRIGHT_VERSION" ]]; then
    c1_log 'installing Chromium system dependencies'
    local sudo=''; [[ $(id -u) -eq 0 ]] || sudo='sudo -n'
    # shellcheck disable=SC2086  # $sudo is intentionally empty or two words
    (cd "$fe" && c1_ienv $sudo env "PATH=$PATH" npx --no-install playwright install-deps chromium >/dev/null) || return 1
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

# N-1: check ALL retained session evidence before a new run or preflight,
# even when cleanup removed the active file before verifying the survivors.
# Never attempt recovery while a verified launcher is still active.
c1_recover_before_start() {
  local root=$1 status
  status=$(c1_session_status "$root") || return 1
  if [[ "$status" == active ]]; then
    c1_err 'another C1 session is active; finish it before starting'
    return 1
  fi
  if [[ "$status" == stale ]]; then c1_warn 'recovering from an interrupted C1 session'; fi
  # Unconditionally scan session directories and orphan PID/inode registers.
  # c1_cleanup_all fails closed if any evidence cannot be retired safely.
  c1_cleanup_all "$root" || {
    c1_err 'unresolved cleanup evidence blocks a new C1 session; inspect and retry cleanup'
    return 1
  }
}

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
  # L-3: keep the observed listener inodes so c1_verify_cleanup can prove none
  # of them is still listening after the session directory is gone.
  if [[ -s "$s/listen-inodes" ]]; then
    if ! ( umask 077; LC_ALL=C sort -u "$s/listen-inodes" >>"$root/.${s##*/}.inodes" ); then
      c1_err 'cleanup evidence: cannot preserve listener inode record; session retained'
      return 1
    fi
  fi
  # ...and the recorded "pid starttime" pairs, so a recorded process that cannot
  # be signalled (it lost its marker) is still reported as surviving.
  if [[ -s "$s/pids" ]]; then
    if ! ( umask 077; cat -- "$s/pids" >>"$root/.${s##*/}.pids" ); then
      c1_err 'cleanup evidence: cannot preserve recorded process identities; session retained'
      return 1
    fi
  fi
  if [[ -d "$s" ]]; then c1_safe_rm "$s" "$root" || return 1; fi
  if [[ -f "$root/active" ]]; then
    local as; as=$(c1_session_dir "$root" || true)
    if [[ "$as" == "$s" || ! -d "${as:-/nonexistent}" ]]; then rm -f -- "$root/active"; fi
  fi
  return 0
}

# c1_cleanup_all ROOT  remove every session directory under ROOT
c1_cleanup_all() {
  local root=$1 s record name failed=0
  local -A checked=()
  [[ -d "$root" ]] || return 0
  for s in "$root"/session.*; do
    [[ -d "$s" ]] || continue
    if ! c1_cleanup_session "$root" "$s"; then failed=1; continue; fi
    c1_verify_cleanup "$s" || failed=1
    checked[$s]=1
  done
  # A crash may leave only the post-cleanup inode/PID registers. Verify them,
  # never discard them to make a subsequent cleanup appear successful.
  for record in "$root"/.session.*.inodes "$root"/.session.*.pids; do
    [[ -e "$record" ]] || continue
    name=${record##*/}; name=${name#.}; s="$root/${name%.*}"
    [[ -n "${checked[$s]:-}" ]] && continue
    c1_verify_cleanup "$s" || failed=1
    checked[$s]=1
  done
  (( failed == 0 )) || { c1_err 'cleanup evidence: verification failed; records retained'; return 1; }
  rm -f -- "$root/active"
}
# c1_verify_cleanup SESSION -> 0 if no marked processes, no session files, ports
# 6080/5173 provably closed and no listener inode recorded during the session
# still listening (L-3). Unavailable listener evidence is a cleanup FAILURE.
c1_verify_cleanup() {
  local s=$1 ok=0 rc reg ino addr
  local -A live=()
  [[ -z "$(c1_marked_pids "$s")" ]] || { c1_err 'harness processes still running'; ok=1; }
  [[ ! -e "$s" ]] || { c1_err 'session directory still present'; ok=1; }
  rc=0; c1_port_free "$C1_NOVNC_PORT" || rc=$?
  (( rc == 0 )) || { if (( rc == 2 )); then c1_err "cannot prove port $C1_NOVNC_PORT is closed (listener evidence unavailable)"; else c1_err "port $C1_NOVNC_PORT still listening"; fi; ok=1; }
  rc=0; c1_port_free "$C1_APP_PORT" || rc=$?
  (( rc == 0 )) || { if (( rc == 2 )); then c1_err "cannot prove port $C1_APP_PORT is closed (listener evidence unavailable)"; else c1_err "port $C1_APP_PORT still listening"; fi; ok=1; }
  local preg pid st
  preg="$(dirname -- "$s")/.${s##*/}.pids"
  if [[ -e "$preg" ]]; then
    local unresolved=0 extra
    if [[ ! -f "$preg" || ! -r "$preg" ]]; then
      c1_err 'preserved PID evidence is not a readable regular file'
      unresolved=1
    else
      while IFS= read -r line || [[ -n "$line" ]]; do
        pid='' st='' extra=''
        read -r pid st extra <<<"$line" || true
        if [[ ! "$pid" =~ ^[0-9]+$ || ! "$st" =~ ^[0-9]+$ || -n "$extra" ]]; then
          c1_err 'malformed preserved PID evidence; record retained'
          unresolved=1
          continue
        fi
        if [[ "$(c1_proc_start "$pid")" == "$st" && ! "$(c1_proc_stat_field "$pid" 1)" =~ ^[ZX]$ ]]; then
          c1_err "recorded harness process $pid ($(c1_safe_comm "$pid")) is still running"
          unresolved=1
        fi
      done <"$preg"
    fi
    if (( unresolved )); then ok=1; else rm -f -- "$preg" || ok=1; fi
  fi
  reg="$(dirname -- "$s")/.${s##*/}.inodes"
  if [[ -e "$reg" ]]; then
    if c1_listen_tables; then
      while read -r ino addr; do [[ -n "$ino" ]] && live[$ino]=$addr; done <<<"$C1_LT_PROC"$'\n'"$C1_LT_SS"
      while read -r ino; do
        [[ "$ino" =~ ^[0-9]+$ ]] || continue
        [[ -z "${live[$ino]:-}" ]] || { c1_err "harness listener inode $ino is still listening on ${live[$ino]}"; ok=1; }
      done <"$reg"
    else
      c1_err 'cannot prove recorded harness listeners are closed (listener evidence unavailable)'; ok=1
    fi
    (( ok )) || rm -f -- "$reg"
  fi
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
# H-1: `-rfbport 0` disables only the IPv4 listener. LibVNCServer defaults
# ipv6port=5900 and binds the wildcard [::]:5900 unless `-rfbportv6 0` is also
# given (`-no6`/`-noipv6` alone were verified insufficient). Both are passed.
c1_cmd_x11vnc() { printf '%s\n' x11vnc -display "$1" -auth "$2/Xauthority" -rfbport 0 -rfbportv6 0 -no6 -unixsock "$2/vnc.sock" \
                    -passwdfile "rm:$2/vncpass" -forever -noxdamage -nolookup -norc -nossl -quiet -o "$2/log/x11vnc.log"; }
c1_cmd_websockify() { printf '%s\n' websockify --web "$C1_NOVNC_WEB" --unix-target="$2/vnc.sock" "127.0.0.1:$1"; }

# c1_close_inherited_fds -> close every fd above 2 in the CURRENT shell (L-1).
# Used only inside c1_spawn's child subshell, so a harness process can never
# inherit (and be misattributed with, or expose) a listening socket of its parent.
c1_close_inherited_fds() {
  local f n
  local -a fds=()
  for f in "/proc/${BASHPID:-$$}/fd/"*; do fds+=("${f##*/}"); done
  for n in "${fds[@]}"; do
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > 2 )) || continue
    exec {n}>&-
  done
}

# c1_spawn SESSION NAME CMD...  start detached in own process group, record pid
c1_spawn() {
  local s=$1 name=$2; shift 2
  ( c1_close_inherited_fds; exec env C1_HARNESS_SESSION="$s" setsid "$@" ) >"$s/log/$name.log" 2>&1 </dev/null &
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
  c1_assert_no_listeners_before_novnc "$s"
}

# c1_assert_no_listeners_before_novnc SESSION -> nothing attributable to the
# session may listen on TCP (IPv4 or IPv6, any address) before noVNC starts.
# Fails closed when the evidence is unavailable. Offending rows (address, pid,
# comm, inode; never environ/argv) go to the terminal and the evidence (M-2);
# marked processes not started by the desktop are reported (L-2).
c1_assert_no_listeners_before_novnc() {
  local s=$1 got orph
  orph=$(c1_harness_orphans "$s")
  if [[ -n "$orph" ]]; then c1_warn "marked processes not started by the desktop: ${orph//$'\n'/; }"; fi
  if ! got=$(c1_harness_listeners "$s"); then
    c1_err 'listener evidence unavailable before noVNC start; failing closed'
    c1_listener_state "$s" failed-unavailable 'pre-novnc evidence unavailable'
    return 1
  fi
  if [[ -n "$got" ]]; then
    c1_err 'unexpected TCP listener before noVNC start:'
    printf '[c1]   %s\n' "${got//$'\n'/$'\n'[c1]   }" >&2
    c1_listener_state "$s" failed-unexpected "pre-novnc: $got${orph:+; orphans: $orph}"
    return 1
  fi
}

# c1_start_websockify SESSION PORT -> noVNC/websockify on 127.0.0.1:PORT only; prints pid.
# On failure after the spawn, the spawned websockify is torn down (returns 1),
# or 2 if the port could not be verified closed afterwards.
c1_start_websockify() {
  local s=$1 port=$2 pid st
  local -a cmd
  c1_port_free "$port" || { c1_err "port $port is already in use; run: c1-session.sh cleanup"; return 1; }
  mapfile -t cmd < <(c1_cmd_websockify "$port" "$s")
  pid=$(c1_spawn "$s" websockify "${cmd[@]}")
  st=$(c1_proc_start "$pid")
  for _ in $(seq 1 50); do c1_port_free "$port" || break; sleep 0.1; done
  if c1_port_free "$port"; then c1_err 'noVNC/websockify failed to listen'
  elif c1_assert_desktop_listeners "$s" "$port"; then printf '%s' "$pid"; return 0
  fi
  c1_teardown_novnc "$s" "$port" "$pid" "$st" websockify-start || return 2
  return 1
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
  local s=$1 port=$2 got addrs
  if ! got=$(c1_harness_listeners "$s"); then
    c1_err 'listener evidence unavailable after noVNC start; failing closed'
    c1_listener_state "$s" failed-unavailable 'desktop evidence unavailable'
    return 1
  fi
  addrs=$(awk 'NF {print $1}' <<<"$got" | LC_ALL=C sort -u)
  if ! c1_assert_loopback_only <<<"$addrs" || [[ "$addrs" != "127.0.0.1:$port" ]]; then
    c1_err "unexpected harness listener set (expected exactly 127.0.0.1:$port):"
    printf '[c1]   %s\n' "${got:-none}" | sed '2,$s/^/[c1]   /' >&2
    c1_listener_state "$s" failed-unexpected "desktop: ${got:-none}"
    return 1
  fi
  c1_listener_state "$s" verified "$got"
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
C1_PROBE_MAX_TIME=15
C1_NOVNC_RETRIES=10
C1_NOVNC_SIGNATURE='noVNC'
C1_PRIVACY_DETAIL=''

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

# c1_https_probe URL OUTFILE [HEADERFILE] [CACERT] -> prints "CODE REDIRECT_URL"
# (CODE 000 on transport failure; REDIRECT_URL empty unless a 3xx carried one).
# HTTPS only, no redirects followed, no proxy, minimal environment (no
# inherited CURL_CA_BUNDLE/HTTPS_PROXY).
c1_https_probe() {
  local url=$1 out=$2 hdr=${3:-} ca=${4:-}
  local -a a=(--silent --max-time "$C1_PROBE_MAX_TIME" --proto '=https' --noproxy '*' -o "$out" -w '%{http_code} %{redirect_url}')
  if [[ -n "$hdr" ]]; then a+=(-H "@$hdr"); fi
  if [[ -n "$ca" ]]; then a+=(--cacert "$ca"); fi
  : >"$out"
  env -i PATH="$PATH" curl "${a[@]}" -- "$url" 2>/dev/null || true
}

# c1_unauth_denied CODE [REDIRECT_URL] -> 0 only for a POSITIVE denial (M-2a):
# HTTP 401/403, or a 30x redirect to https://github.com or an https subdomain
# of github.com (GitHub sign-in), default port, no userinfo. Everything else
# (000 reset/timeout/DNS/TLS failure, 5xx, other 4xx, a redirect anywhere else,
# a malformed or missing Location) is ambiguous and NOT a denial.
c1_unauth_denied() {
  local code=$1 loc=${2:-} host
  case $code in
    401|403) return 0 ;;
    301|302|303|307|308)
      [[ "$loc" =~ ^https://([A-Za-z0-9.-]+)(:443)?(/[^[:space:]]*)?$ ]] || return 1
      host=${BASH_REMATCH[1],,}
      [[ "$host" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)*github\.com$ ]] ;;
    *) return 1 ;;
  esac
}

# c1_privacy_check BASE PATH MARKER TOKENHDR [CACERT]
# 0 = verified private. Sets C1_PRIVACY_STATE to private | public | unverified
# (and C1_PRIVACY_DETAIL to a short, token-free reason). Private requires BOTH a
# positive unauthenticated denial AND an authenticated 200 carrying the marker.
c1_privacy_check() {
  local base=$1 path=$2 marker=$3 hdr=$4 ca=${5:-} f code loc
  C1_PRIVACY_STATE=unverified C1_PRIVACY_DETAIL='bad arguments'
  [[ -n "$base" && -n "$marker" && -s "$hdr" ]] || return 1
  f=$(mktemp "${TMPDIR:-/tmp}/c1probe.XXXXXXXX") || return 1
  read -r code loc <<<"$(c1_https_probe "$base$path" "$f" "" "$ca")" || true
  code=${code:-000}
  if grep -qF -- "$marker" "$f"; then C1_PRIVACY_STATE=public C1_PRIVACY_DETAIL="unauthenticated probe received the marker (HTTP $code)"; rm -f "$f"; return 1; fi
  # Any 2xx to an unauthenticated client is treated as exposure (fail closed).
  if [[ "$code" == 2?? ]]; then C1_PRIVACY_STATE=public C1_PRIVACY_DETAIL="unauthenticated probe got HTTP $code"; rm -f "$f"; return 1; fi
  if ! c1_unauth_denied "$code" "${loc:-}"; then
    C1_PRIVACY_DETAIL="unauthenticated probe was not positively denied (HTTP $code)"; rm -f "$f"; return 1
  fi
  read -r code loc <<<"$(c1_https_probe "$base$path" "$f" "$hdr" "$ca")" || true
  if [[ "${code:-}" == 200 ]] && grep -qF -- "$marker" "$f"; then C1_PRIVACY_STATE=private C1_PRIVACY_DETAIL='denied unauthenticated; authenticated probe reached the marker'; rm -f "$f"; return 0; fi
  C1_PRIVACY_DETAIL="authenticated probe did not reach the marker (HTTP ${code:-000})"
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
  for (( i = 0; i < C1_NOVNC_RETRIES; i++ )); do
    c1_privacy_check "$1" '/vnc.html' "$C1_NOVNC_SIGNATURE" "$2" "${3:-}" && return 0
    [[ $C1_PRIVACY_STATE == public ]] && return 1
    sleep 2
  done
  return 1
}

# c1_teardown_novnc SESSION PORT WSPID WSSTART [REASON]
# Shared by the monitor and the launcher's privacy gate (L-9). Writes the
# privacy-failed marker FIRST, then TERM -> wait -> KILL (ownership-verified),
# and verifies that PORT no longer listens. Returns 1 (and records
# "teardown-failed") if the port is still open: callers must fail closed.
c1_teardown_novnc() {
  local s=$1 port=$2 ws=$3 wsst=$4 reason=${5:-unverified}
  # A monitor supplies a tighter local bound; other callers retain the 2 s
  # TERM / KILL grace periods. Never accept arbitrary external step counts.
  local wait_steps=20
  [[ "${C1_MONITOR_FAST_TEARDOWN:-}" == 1 ]] && wait_steps=5
  [[ "$reason" =~ ^[a-z0-9_-]{1,40}$ ]] || reason=unverified
  printf '%s %s\n' "$(c1_utc)" "$reason" >>"$s/privacy-failed"
  c1_signal_owned TERM "$ws" "$s" "$wsst"
  for (( _=0; _<wait_steps; _++ )); do
    if c1_port_free "$port" && ! c1_owned_pid "$ws" "$s" "$wsst"; then break; fi
    sleep 0.1
  done
  c1_signal_owned KILL "$ws" "$s" "$wsst"
  for (( _=0; _<wait_steps; _++ )); do c1_port_free "$port" && break; sleep 0.1; done
  if ! c1_port_free "$port"; then
    printf '%s teardown-failed\n' "$(c1_utc)" >>"$s/privacy-failed"
    c1_err "port $port is still listening after the privacy teardown"
    return 1
  fi
}

# c1_gh_private PORT -> 0 if gh reads back "private"; 1 if gh reports anything
# else (C1_GH_VIS=<sanitized value>); 2 if the read-back is unavailable.
c1_gh_private() {
  local v; C1_GH_VIS=''
  v=$(c1_gh_visibility "$1") || v=''
  [[ -n "$v" ]] || return 2
  [[ "$v" =~ ^[a-z_-]{1,24}$ ]] || v=unknown
  C1_GH_VIS=$v
  [[ "$v" == private ]] || return 1
}

# c1_privacy_gate SESSION PORT BASE TOKENHDR WSPID WSSTART REQUIRE_GH [CACERT]
# 0 = noVNC still verified private (and, when REQUIRE_GH=1, gh still reads back
# "private"). Otherwise noVNC is torn down: 1 = torn down and port verified
# closed; 2 = teardown could NOT be verified (caller must abort the session).
c1_privacy_gate() {
  local s=$1 port=$2 base=$3 hdr=$4 ws=$5 wsst=$6 req_gh=${7:-0} ca=${8:-} reason='' g=0
  if [[ -e "$s/privacy-failed" ]]; then reason=monitor-flagged
  elif ! c1_verify_private_novnc "$base" "$hdr" "$ca"; then reason=$C1_PRIVACY_STATE
  elif (( req_gh )); then
    c1_gh_private "$port" || g=$?
    if (( g == 1 )); then reason="gh-visibility-$C1_GH_VIS"; elif (( g == 2 )); then reason=gh-unavailable; fi
  fi
  [[ -n "$reason" ]] || return 0
  C1_GATE_REASON=$reason
  c1_teardown_novnc "$s" "$port" "$ws" "$wsst" "$reason" || return 2
  return 1
}

# c1_privacy_monitor_loop SESSION BASE TOKENHDR WSPID WSSTART [CACERT] [INTERVAL] [PORT] [REQUIRE_GH]
# Background, marker-scoped: tears noVNC down (and flags the session) as soon as
# the port is seen public or gh (REQUIRE_GH=1, L-10) reads back anything other
# than "private" (e.g. org/public), or on the first unverifiable
# observation (ambiguous HTTPS probe or unavailable gh read-back).
c1_privacy_monitor_loop() {
  local s=$1 base=$2 hdr=$3 ws=$4 wsst=$5 ca=${6:-} iv=${7:-0.2} port=${8:-$C1_NOVNC_PORT} req_gh=${9:-0}
  # L-6: these are local to this monitor's shell/function calls; bootstrap
  # privacy checks still use their full timeouts. One sweep is bounded to
  # two HTTPS probes at <=0.4 s each; teardown gets <=1 s of grace.
  local C1_PROBE_MAX_TIME=0.4 C1_MONITOR_FAST_TEARDOWN=1
  local reason g
  trap 'exit 0' TERM
  while c1_owned_pid "$ws" "$s" "$wsst"; do
    sleep "$iv"
    reason=''
    if c1_privacy_check "$base" '/vnc.html' "$C1_NOVNC_SIGNATURE" "$hdr" "$ca"; then
      if (( ! req_gh )); then continue; fi
      g=0; c1_gh_private "$port" || g=$?
      if (( g == 0 )); then continue; fi
      if (( g == 1 )); then reason="gh-visibility-$C1_GH_VIS"; else reason=gh-unavailable; fi
    elif [[ $C1_PRIVACY_STATE == public ]]; then reason=public
    else reason=unverified
    fi
    # A stalled/ambiguous edge is not a positive privacy proof. Fail closed
    # on its first observation: three retry cycles cannot meet L-6's bound.
    if [[ -n "$reason" ]]; then
      # L-9: an unverifiable teardown must propagate failure to the watchdog.
      c1_teardown_novnc "$s" "$port" "$ws" "$wsst" "$reason" || return 2
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
  read -r code _ <<<"$(c1_https_probe "$api/repos/$repo" "$f" "$hdr" "$ca")" || true
  if [[ "${code:-}" == 200 ]]; then
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
  while [[ "$(c1_proc_start "$lpid")" == "$lstart" && ! "$(c1_proc_stat_field "$lpid" 1)" =~ ^[ZX]$ ]]; do
    # L-9: if port closure is unverified, stop only the exact recorded launcher
    # after validating PID/start time and the active-session record.
    if [[ -f "$s/privacy-failed" ]] && grep -q ' teardown-failed$' "$s/privacy-failed"; then
      local active_pid active_start active_session
      if read -r active_pid active_start active_session <"$root/active" &&
         [[ "$active_pid" == "$lpid" && "$active_start" == "$lstart" && "$active_session" == "$s" ]] &&
         [[ "$(c1_proc_start "$lpid")" == "$lstart" ]]; then
        kill -TERM "$lpid" 2>/dev/null || true
      fi
      break
    fi
    sleep 1
  done
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

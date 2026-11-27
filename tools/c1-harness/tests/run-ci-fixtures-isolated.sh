#!/usr/bin/env bash
# GitHub-hosted Ubuntu only. Install dependencies before entering this namespace.
# This is network isolation for reviewed fixtures, not a general code sandbox.
set -euo pipefail

fail() { printf 'C1 fixture isolation: %s\n' "$1" >&2; exit 1; }
[[ $# == 1 && ( $1 == browser || $1 == harness ) ]] || fail 'choose browser or harness'
[[ ${C1_LIVE:-0} == 0 && ${C1_MUTATION_MODE:-0} == 0 ]] || fail 'live or mutation mode supplied at entry'
uid=$(/usr/bin/id -u)
gid=$(/usr/bin/id -g)
(( uid != 0 && gid != 0 )) || fail 'fixtures must run as the non-root runner'
parent_net=$(/usr/bin/readlink /proc/self/ns/net)
repo=$(cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)

# The privileged phase runs fixed system commands only, never repository tests.
# Both ends of the veth are created INSIDE the new namespace, with no peer
# outside it. A non-loopback address keeps the existing STUN control meaningful.
bootstrap=$(/usr/bin/cat <<'BOOTSTRAP'
set -euo pipefail
parent_net=$1; shift
[[ $(/usr/bin/readlink /proc/self/ns/net) != "$parent_net" ]]
/usr/sbin/ip link set lo up
/usr/sbin/ip link add c1test0 type veth peer name c1peer0
/usr/sbin/ip address add 192.0.2.1/32 dev c1test0
/usr/sbin/ip link set c1test0 up
/usr/sbin/ip link set c1peer0 up
uid=$1; gid=$2; shift 2
exec /usr/bin/setpriv --reuid="$uid" --regid="$gid" --clear-groups \
  --no-new-privs --inh-caps=-all --ambient-caps=-all --bounding-set=-all -- "$@"
BOOTSTRAP
)

fixtures=$(/usr/bin/cat <<'FIXTURES'
set -euo pipefail
parent_net=$1; repo=$2; suite=$3
die() { printf 'C1 fixture isolation: %s\n' "$1" >&2; exit 1; }
# Fail closed before executing any fixture if the boundary is wrong.
current_net=$(/usr/bin/readlink /proc/self/ns/net)
[[ $EUID != 0 && $current_net != "$parent_net" ]] || die 'runner is root or still in the parent network namespace'
/usr/bin/awk '
  $1 == "NoNewPrivs:" { nnp = ($2 == 1) }
  $1 ~ /^Cap(Inh|Prm|Eff|Bnd|Amb):$/ { caps++; if ($2 !~ /^0+$/) bad = 1 }
  END { exit !(nnp && caps == 5 && !bad) }
' /proc/self/status || die 'process capabilities remain or privilege escalation is possible'
links=$(/usr/sbin/ip -o link show | /usr/bin/awk -F": " '{sub(/@.*/, "", $2); print $2}' | /usr/bin/sort)
[[ $links == $'c1peer0\nc1test0\nlo' ]] || die 'unexpected network interfaces'
routes4=$(/usr/sbin/ip -4 route show default)
routes6=$(/usr/sbin/ip -6 route show default)
[[ -z $routes4 && -z $routes6 ]] || die 'unexpected default route'
/usr/sbin/ip -o -4 address show dev c1test0 | /usr/bin/grep -Fq '192.0.2.1/32'
printf '%s\n' 'C1 fixture isolation verified: private network, no default routes, no process capabilities'

case "$suite" in
  browser)
    cd "$repo/frontend"
    ./node_modules/.bin/playwright test --config=playwright.config.mjs --workers=1
    C1_UNCONFIGURED=1 ./node_modules/.bin/playwright test --config=playwright.config.mjs --workers=1
    node e2e/c1-mutation-check.mjs
    node e2e/run-browser-mutations.mjs
    # Unrestricted control must deliver local STUN. Guarded control must not.
    xvfb-run -a node e2e/c1-webrtc-udp-check.mjs
    ;;
  harness)
    cd "$repo"
    bash tools/c1-harness/c1-session.sh --help
    rc=0
    bash tools/c1-harness/c1-session.sh || rc=$?
    test "$rc" -eq 78
    bash tools/c1-harness/tests/test_c1_lifecycle.sh
    bash tools/c1-harness/tests/test_c1_lib.sh
    node --test tools/c1-harness/tests/*.test.mjs
    ;;
  *) exit 2 ;;
esac
FIXTURES
)

[[ -x /usr/bin/sudo ]] || fail 'sudo unavailable; no unisolated fallback'
# No checkout PATH, secrets, proxy settings, or shell startup files in the
# privileged phase. Preserve the runner's Node and browser cache paths only
# in the unprivileged process environment.
exec /usr/bin/sudo -n -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/unshare --net -- /bin/bash --noprofile --norc -c "$bootstrap" c1-bootstrap \
  "$parent_net" "$uid" "$gid" /usr/bin/env -i "PATH=$PATH" "HOME=$HOME" \
  LANG=C.UTF-8 CI=true C1_LIVE=0 C1_UNCONFIGURED=0 C1_MUTATION_MODE=0 npm_config_offline=true \
  /bin/bash --noprofile --norc -c "$fixtures" c1-fixtures "$parent_net" "$repo" "$1"

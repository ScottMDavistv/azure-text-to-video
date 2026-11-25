#!/usr/bin/env bash
set -euo pipefail
umask 077
stop() { printf 'TKT90 preflight=%s\n' "$1"; exit 1; }
[[ ${1-} == --one-approved-run && $# == 1 ]] || stop explicit-run-flag-required
archive=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -n ${TKT90_CHECKOUT:-} ]] || stop checkout-required
cd -- "$TKT90_CHECKOUT" 2>/dev/null || stop checkout-missing
[[ $(git rev-parse HEAD 2>/dev/null) == 606dd91663b8bc63f87773dd70988898d55572d0 ]] || stop wrong-commit
if git symbolic-ref --quiet HEAD >/dev/null; then stop detached-checkout-required; fi
[[ -z $(git status --porcelain --untracked-files=all) ]] || stop checkout-not-clean
[[ -d frontend/node_modules ]] || stop local-frontend-dependencies-missing
for tool in node ffmpeg ffprobe; do command -v "$tool" >/dev/null || stop local-runtime-missing; done
python=${TKT90_PYTHON:-python3}
command -v "$python" >/dev/null || stop local-python-missing
for key in TKT90_TENANT TKT90_API TKT90_SPA; do
    [[ ${!key-} =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ && ${!key} != 00000000-* ]] || stop dev-entra-config-required
done
[[ $TKT90_API != "$TKT90_SPA" ]] || stop distinct-apps-required
curl=${TKT90_WINDOWS_CURL:-/mnt/c/Windows/System32/curl.exe}
[[ -f $curl && -x $curl ]] || stop windows-curl-missing
exec env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 TERM="${TERM:-xterm}" \
    TKT90_TENANT="$TKT90_TENANT" TKT90_API="$TKT90_API" TKT90_SPA="$TKT90_SPA" \
    TKT90_WINDOWS_CURL="$curl" "$python" -I "$archive/run.py"

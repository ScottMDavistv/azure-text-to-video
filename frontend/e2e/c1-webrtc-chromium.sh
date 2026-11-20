#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
browser=$(node "$here/c1-webrtc-policy.mjs" "$@")
[[ "$browser" = /* && -x "$browser" ]]
# Preserve Playwright's inherited remote-debugging pipe on descriptors 3 and 4.
exec "$browser" "$@"

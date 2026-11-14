#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SHA="$(git rev-parse HEAD)"

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid Git commit SHA"
    exit 1
fi

echo "acrtextvideo1794671698.azurecr.io/textvideo-worker:$SHA"

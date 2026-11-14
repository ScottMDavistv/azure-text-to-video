#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Worker deployment readiness ==="

test -f backend/Dockerfile.worker
test -f backend/worker_runtime.py
test -f backend/worker_readiness.py
test -f backend/requirements.txt

echo "=== Current Git commit ==="
git rev-parse --short HEAD

echo "=== Live provider selection ==="
echo "Configuration is checked without printing secrets"

PYTHONPATH=backend python backend/worker_runtime.py --check

echo "=== Required build files present ==="
echo "Worker image can be built independently"

echo "READINESS INSPECTION COMPLETE"
echo "No Azure changes or rendering requests performed"

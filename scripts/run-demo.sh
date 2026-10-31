#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${ADMIN_TOKEN:-}" ]]; then
  echo 'Set ADMIN_TOKEN for your isolated local demo backend before running.' >&2
  exit 2
fi
uv run python -m eval.runner --base-url "${DEMO_BASE_URL:-http://127.0.0.1:8000}" --backend rules --split dev --repetitions 3 "$@"

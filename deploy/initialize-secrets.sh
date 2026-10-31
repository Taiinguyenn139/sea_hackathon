#!/usr/bin/env bash
set -euo pipefail
# First-time only: generated credentials flow directly into Secret Manager.
PROJECT=${1:?Confirmed GCP project ID required}
ACCOUNT=${GCP_ACCOUNT:?Set the authorized GCP account}
gc() { gcloud "$@" --project="$PROJECT" --account="$ACCOUNT" --quiet; }
gc secrets describe memory-harness-runtime >/dev/null
EXISTING=$(gc secrets versions list memory-harness-runtime --limit=1 --format='value(name)')
if test -n "$EXISTING"; then
  echo 'Secret already has a version; preserving it. Rotate explicitly if needed.'
  exit 0
fi
python3 -c 'import secrets; print("ADMIN_TOKEN=" + secrets.token_urlsafe(32)); print("STAFF_PASSWORD=" + secrets.token_urlsafe(32)); print("LLM_BACKEND=rules")' | \
  gc secrets versions add memory-harness-runtime --data-file=- >/dev/null
echo 'Initial credentials stored in Secret Manager; no values printed.'

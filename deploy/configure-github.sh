#!/usr/bin/env bash
set -euo pipefail
# Run after VM bootstrap, runtime credentials and the HTTPS proxy are ready.
PROJECT=${1:?Confirmed project ID required}
APP_URL=${2:?Public HTTPS URL required}
ACCOUNT=${GCP_ACCOUNT:?Set the authorized GCP account}
REPO=Taiinguyenn139/sea_hackathon
[[ "$PROJECT" =~ ^[a-z][a-z0-9-]+$ ]] || exit 2
[[ "$APP_URL" =~ ^https://[a-zA-Z0-9.-]+$ ]] || exit 2
NUMBER=$(gcloud projects describe "$PROJECT" --account="$ACCOUNT" --project="$PROJECT" --format='value(projectNumber)')
test -n "$NUMBER"
# Environment creation preserves existing protection settings when present.
if ! gh api "repos/$REPO/environments/production" >/dev/null 2>&1; then
  gh api --method PUT "repos/$REPO/environments/production" >/dev/null
fi
setvar() { gh variable set "$1" --repo "$REPO" --env production --body "$2"; }
setvar GCP_PROJECT_ID "$PROJECT"
setvar GCP_REGION "${GCP_REGION:-asia-southeast1}"
setvar GCP_ZONE "${GCP_ZONE:-asia-southeast1-b}"
setvar GCP_INSTANCE memory-harness
setvar GCP_ARTIFACT_REPOSITORY memory-harness
setvar GCP_WORKLOAD_IDENTITY_PROVIDER "projects/$NUMBER/locations/global/workloadIdentityPools/github-harness/providers/github"
setvar GCP_DEPLOY_SERVICE_ACCOUNT "harness-deploy@$PROJECT.iam.gserviceaccount.com"
setvar APP_URL "$APP_URL"
# This repository-level switch is readable before the production job starts.
gh variable set GCP_DEPLOY_ENABLED --repo "$REPO" --body true
echo 'Production variables configured; main pushes can now deploy.'

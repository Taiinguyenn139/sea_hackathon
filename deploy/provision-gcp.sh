#!/usr/bin/env bash
set -euo pipefail
# Creates billable resources. Run only after the project destination is confirmed.
PROJECT=${1:?Confirmed GCP project ID required}
ACCOUNT=${GCP_ACCOUNT:?Set the authorized GCP account}
REGION=${GCP_REGION:-asia-southeast1}
ZONE=${GCP_ZONE:-asia-southeast1-b}
NAME=memory-harness
[[ "$PROJECT" =~ ^[a-z][a-z0-9-]+$ ]] || exit 2
gc() { gcloud "$@" --project="$PROJECT" --account="$ACCOUNT" --quiet; }
test "$(gc billing projects describe "$PROJECT" --format='value(billingEnabled)')" = True
gc services enable compute.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com iap.googleapis.com oslogin.googleapis.com
NUMBER=$(gc projects describe "$PROJECT" --format='value(projectNumber)')
RUNTIME="harness-runtime@$PROJECT.iam.gserviceaccount.com"
DEPLOY="harness-deploy@$PROJECT.iam.gserviceaccount.com"
for sa in harness-runtime harness-deploy; do
  gc iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gc iam service-accounts create "$sa" --display-name="$sa"
done
gc artifacts repositories describe "$NAME" --location="$REGION" >/dev/null 2>&1 || \
  gc artifacts repositories create "$NAME" --location="$REGION" --repository-format=docker
gc artifacts repositories add-iam-policy-binding "$NAME" --location="$REGION" \
  --member="serviceAccount:$RUNTIME" --role=roles/artifactregistry.reader >/dev/null
gc artifacts repositories add-iam-policy-binding "$NAME" --location="$REGION" \
  --member="serviceAccount:$DEPLOY" --role=roles/artifactregistry.writer >/dev/null
gc iam workload-identity-pools describe github-harness --location=global >/dev/null 2>&1 || \
  gc iam workload-identity-pools create github-harness --location=global --display-name='GitHub Memory Harness'
CONDITION="assertion.repository_id == '1440420431' && assertion.repository_owner_id == '51513839' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == 'Taiinguyenn139/sea_hackathon/.github/workflows/deploy-gcp.yml@refs/heads/main'"
if ! gc iam workload-identity-pools providers describe github --workload-identity-pool=github-harness --location=global >/dev/null 2>&1; then
  gc iam workload-identity-pools providers create-oidc github --workload-identity-pool=github-harness \
    --location=global --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping='google.subject=assertion.sub,attribute.repository_id=assertion.repository_id' \
    --attribute-condition="$CONDITION"
else
  test "$(gc iam workload-identity-pools providers describe github --workload-identity-pool=github-harness --location=global --format='value(attributeCondition)')" = "$CONDITION" || { echo 'Existing provider condition differs; review before changing'; exit 1; }
fi
gc iam service-accounts add-iam-policy-binding "$DEPLOY" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/github-harness/attribute.repository_id/1440420431" >/dev/null
gc iam service-accounts add-iam-policy-binding "$RUNTIME" --role=roles/iam.serviceAccountUser \
  --member="serviceAccount:$DEPLOY" >/dev/null
gc compute networks describe "$NAME" >/dev/null 2>&1 || gc compute networks create "$NAME" --subnet-mode=custom
gc compute networks subnets describe "$NAME" --region="$REGION" >/dev/null 2>&1 || \
  gc compute networks subnets create "$NAME" --network="$NAME" --region="$REGION" --range=10.80.0.0/24
gc compute firewall-rules describe harness-web >/dev/null 2>&1 || \
  gc compute firewall-rules create harness-web --network="$NAME" --allow=tcp:80,tcp:443 --target-tags=harness-web --source-ranges=0.0.0.0/0
gc compute firewall-rules describe harness-iap >/dev/null 2>&1 || \
  gc compute firewall-rules create harness-iap --network="$NAME" --allow=tcp:22 --target-tags=harness-web --source-ranges=35.235.240.0/20
gc compute addresses describe "$NAME" --region="$REGION" >/dev/null 2>&1 || \
  gc compute addresses create "$NAME" --region="$REGION"
IP=$(gc compute addresses describe "$NAME" --region="$REGION" --format='value(address)')
gc compute disks describe harness-data --zone="$ZONE" >/dev/null 2>&1 || \
  gc compute disks create harness-data --zone="$ZONE" --size=20GB --type=pd-balanced
if ! gc compute instances describe "$NAME" --zone="$ZONE" >/dev/null 2>&1; then
  gc compute instances create "$NAME" --zone="$ZONE" --machine-type=e2-small \
    --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=10GB \
    --subnet="$NAME" --address="$IP" --tags=harness-web \
    --service-account="$RUNTIME" --scopes=https://www.googleapis.com/auth/cloud-platform \
    --disk=name=harness-data,device-name=harness-data,mode=rw,boot=no,auto-delete=no \
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
    --metadata-from-file=startup-script=deploy/startup.sh \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
fi
gc compute instances add-iam-policy-binding "$NAME" --zone="$ZONE" \
  --member="serviceAccount:$DEPLOY" --role=roles/compute.osAdminLogin >/dev/null
gc iam roles describe harnessDeployDiscovery >/dev/null 2>&1 || gc iam roles create harnessDeployDiscovery \
  --title='Harness deployment instance discovery' --stage=GA \
  --permissions=compute.instances.get,compute.instances.list,compute.projects.get
gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOY" \
  --role="projects/$PROJECT/roles/harnessDeployDiscovery" --condition=None >/dev/null
PRIVATE_IP=$(gc compute instances describe "$NAME" --zone="$ZONE" --format='value(networkInterfaces[0].networkIP)')
gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOY" \
  --role=roles/iap.tunnelResourceAccessor \
  --condition="expression=destination.port == 22 && destination.ip == '$PRIVATE_IP',title=harness_ssh_only" >/dev/null
printf 'Project: %s\nInstance: %s\nZone: %s\nPublic IP: %s\nWIF provider: projects/%s/locations/global/workloadIdentityPools/github-harness/providers/github\nDeploy service account: %s\n' \
  "$PROJECT" "$NAME" "$ZONE" "$IP" "$NUMBER" "$DEPLOY"
echo 'Provisioned. Runtime secrets, HTTPS proxy and first image release remain separate steps.'

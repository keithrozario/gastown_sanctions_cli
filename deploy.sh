#!/usr/bin/env bash
# =============================================================================
# OFAC SDN Pipeline — Deployment Script
#
# Deploys the full serverless OFAC ingestion pipeline to GCP:
#   1. Enable required APIs (Terraform, targeted apply)
#   2. Create Artifact Registry repo (Terraform)
#   3. Build and push Dataflow Docker image
#   4. Build Dataflow Flex Template spec → upload to GCS
#   5. Full Terraform apply (all infrastructure)
#   6. Create BigQuery search index
#   7. Optional: trigger first manual run
#
# Prerequisites:
#   - gcloud CLI authenticated (or running as a SA with required permissions)
#   - Terraform >= 1.5 installed
#   - Docker available (or use Cloud Build flag below)
#   - Python 3.11+ available
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/terraform"
DATAFLOW_DIR="${SCRIPT_DIR}/dataflow"

# ── Configuration (override with env vars if needed) ─────────────────────────
PROJECT_ID="${PROJECT_ID:-remote-machine-b7af52b6}"
REGION="${REGION:-asia-southeast1}"
ARTIFACT_REPO="${ARTIFACT_REPO:-ofac-pipeline}"
TEMPLATE_BUCKET="${TEMPLATE_BUCKET:-ofac-dataflow-remote-machine-b7af52b6}"
BQ_PROJECT="${BQ_PROJECT:-${PROJECT_ID}}"
BQ_DATASET="${BQ_DATASET:-ofac_sanctions}"
BQ_TABLE="${BQ_TABLE:-sdn_list}"

# Docker image name
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/ofac-pipeline"
IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

# Dataflow Flex Template GCS path
TEMPLATE_GCS_PATH="gs://${TEMPLATE_BUCKET}/templates/ofac-pipeline.json"

# Use Cloud Build instead of local Docker? (set to "true" for environments without Docker)
USE_CLOUD_BUILD="${USE_CLOUD_BUILD:-false}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OFAC SDN Pipeline Deployment"
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Image:    ${FULL_IMAGE}"
echo "  Template: ${TEMPLATE_GCS_PATH}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Terraform init + enable APIs ─────────────────────────────────────
echo ""
echo "▶ Step 1: Terraform init and API enablement"
cd "${TERRAFORM_DIR}"
terraform init -upgrade

echo "  Enabling GCP APIs (targeted apply)..."
terraform apply \
  -target=google_project_service.apis \
  -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

echo "  Waiting 60s for APIs to propagate..."
sleep 60

# ── Step 2: Create Artifact Registry repo ─────────────────────────────────────
echo ""
echo "▶ Step 2: Create Artifact Registry repository"
terraform apply \
  -target=google_artifact_registry_repository.ofac \
  -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

# ── Step 3: Build and push Dataflow Docker image ─────────────────────────────
echo ""
echo "▶ Step 3: Build Dataflow Docker image"
cd "${DATAFLOW_DIR}"

# Configure Docker auth for Artifact Registry
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

if [ "${USE_CLOUD_BUILD}" = "true" ]; then
  echo "  Using Cloud Build to build image..."
  gcloud builds submit \
    --tag="${FULL_IMAGE}" \
    --project="${PROJECT_ID}" \
    .
else
  echo "  Building locally with Docker..."
  docker build -t "${FULL_IMAGE}" .
  echo "  Pushing image to Artifact Registry..."
  docker push "${FULL_IMAGE}"
fi

echo "  Image available at: ${FULL_IMAGE}"

# ── Step 4: Build Dataflow Flex Template ──────────────────────────────────────
echo ""
echo "▶ Step 4: Build Dataflow Flex Template"
gcloud dataflow flex-template build "${TEMPLATE_GCS_PATH}" \
  --image="${FULL_IMAGE}" \
  --sdk-language=PYTHON \
  --metadata-file="${DATAFLOW_DIR}/metadata.json" \
  --project="${PROJECT_ID}"

echo "  Flex Template uploaded to: ${TEMPLATE_GCS_PATH}"

# ── Step 5: Full Terraform apply ──────────────────────────────────────────────
echo ""
echo "▶ Step 5: Full Terraform apply"
cd "${TERRAFORM_DIR}"
terraform apply \
  -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

# ── Step 6: Create BigQuery Search Index ──────────────────────────────────────
echo ""
echo "▶ Step 6: Create BigQuery search index for fuzzy name matching"
bq query \
  --project_id="${BQ_PROJECT}" \
  --use_legacy_sql=false \
  "CREATE SEARCH INDEX IF NOT EXISTS sdn_name_index
   ON \`${BQ_PROJECT}.${BQ_DATASET}.${BQ_TABLE}\` (primary_name.full_name)"

echo "  Search index created."

# ── Step 7: Print deployment summary ─────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deployment Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Resources:"
terraform output 2>/dev/null || true
echo ""
echo "  To trigger ingestion manually:"
echo "    gcloud scheduler jobs run ofac-weekly-ingestion \\"
echo "      --location=${REGION} --project=${PROJECT_ID}"
echo ""
echo "  To monitor Dataflow:"
echo "    gcloud dataflow jobs list --region=${REGION} --project=${PROJECT_ID}"
echo ""
echo "  To query BigQuery:"
echo "    bq query --use_legacy_sql=false \\"
echo "      'SELECT COUNT(*) FROM \`${BQ_PROJECT}.${BQ_DATASET}.${BQ_TABLE}\`'"
echo ""
echo "  Test queries:"
echo "    bq query --use_legacy_sql=false < ${SCRIPT_DIR}/queries/test_queries.sql"

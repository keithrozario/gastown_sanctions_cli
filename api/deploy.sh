#!/usr/bin/env bash
# =============================================================================
# OFAC Screening API — Deployment Script
#
# Steps:
#   1. Build & push ofac-api Docker image via Cloud Build
#   2. terraform apply (creates SA, IAM, Cloud Run service)
#   3. Run local unit tests
#   4. Export CLOUD_RUN_URL and run integration tests
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/terraform"

PROJECT_ID="${PROJECT_ID:-remote-machine-b7af52b6}"
REGION="${REGION:-asia-southeast1}"
ARTIFACT_REPO="${ARTIFACT_REPO:-ofac-pipeline}"

API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/ofac-api:latest"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OFAC Screening API Deployment"
echo "  Project: ${PROJECT_ID}  Region: ${REGION}"
echo "  Image:   ${API_IMAGE}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Build & push via Cloud Build ──────────────────────────────────────
echo ""
echo "▶ Step 1: Build and push ofac-api image"
gcloud builds submit "${SCRIPT_DIR}" \
  --tag="${API_IMAGE}" \
  --project="${PROJECT_ID}"

# ── Step 2: Terraform apply ───────────────────────────────────────────────────
echo ""
echo "▶ Step 2: Terraform apply (SA + IAM + Cloud Run)"
cd "${TERRAFORM_DIR}"
terraform apply \
  -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

# ── Step 2b: Force Cloud Run redeploy ─────────────────────────────────────────
# terraform uses :latest tag and won't redeploy when only the image digest changes
echo ""
echo "▶ Step 2b: Force Cloud Run to pick up new image digest"
gcloud run services update ofac-screening-api \
  --region="${REGION}" \
  --image="${API_IMAGE}" \
  --project="${PROJECT_ID}"

# ── Step 3: Unit tests ────────────────────────────────────────────────────────
echo ""
echo "▶ Step 3: Unit tests (mocked BQ + Vertex)"
cd "${REPO_ROOT}"
api/.venv/bin/pytest api/tests/test_unit.py -v

# ── Step 4: Integration tests ─────────────────────────────────────────────────
echo ""
echo "▶ Step 4: Integration tests (live Cloud Run)"
cd "${TERRAFORM_DIR}"
export CLOUD_RUN_URL
CLOUD_RUN_URL=$(terraform output -raw api_url)
echo "  API URL: ${CLOUD_RUN_URL}"

cd "${REPO_ROOT}"
api/.venv/bin/pytest api/tests/test_integration.py -v

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deployment complete!"
echo "  API URL: ${CLOUD_RUN_URL}"
echo "  Try:  curl \"${CLOUD_RUN_URL}/screen?name=USAMA+BIN+LADIN\""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

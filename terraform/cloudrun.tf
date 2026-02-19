# ─── OFAC Screening API — Cloud Run v2 ───────────────────────────────────────

resource "google_cloud_run_v2_service" "ofac_api" {
  name     = "ofac-screening-api"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repo_name}/ofac-api:latest"
      ports {
        container_port = 8080
      }
      env {
        name  = "BQ_TABLE"
        value = "${var.project_id}.${var.bq_dataset_id}.${var.bq_table_id}"
      }
      env {
        name  = "BQ_PROJECT"
        value = var.project_id
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_service_account.api,
  ]
}

# Note: allUsers invoker is blocked by org policy.
# Grant invoker role to specific identities as needed:
#   gcloud run services add-iam-policy-binding ofac-screening-api \
#     --region=asia-southeast1 \
#     --member="user:YOU@domain.com" \
#     --role=roles/run.invoker

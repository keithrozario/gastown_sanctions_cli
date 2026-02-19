# ─── OFAC Screening API — Cloud Run v2 ───────────────────────────────────────

resource "google_cloud_run_v2_service" "ofac_api" {
  name     = "ofac-screening-api"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

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
      env {
        name  = "VERTEX_REGION"
        value = "us-central1"
      }
      env {
        name  = "VERTEX_MODEL"
        value = "gemini-2.0-flash-001"
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_service_account.api,
  ]
}

# Allow unauthenticated access via the load balancer.
# Cloud Run ingress is restricted to INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER,
# so direct internet access to the Cloud Run URL is blocked.
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ofac_api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

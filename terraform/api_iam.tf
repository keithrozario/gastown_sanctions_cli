# ─── OFAC Screening API — Service Account & IAM ──────────────────────────────

resource "google_service_account" "api" {
  account_id   = "ofac-api"
  display_name = "OFAC Screening API (Cloud Run)"
  project      = var.project_id
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "api_bq_viewer" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.api.email}"
}

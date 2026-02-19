# ─── Project data (for Cloud Build SA) ───────────────────────────────────────
data "google_project" "project" {
  project_id = var.project_id
}

# Grant Cloud Build service account the permissions it needs to build
# Cloud Functions Gen2 (which uses Cloud Build internally)
resource "google_project_iam_member" "cloudbuild_sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "cloudbuild_sa_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "cloudbuild_sa_ar" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

# Cloud Build jobs (gcloud builds submit) run as the Compute Engine default SA,
# so it also needs Artifact Registry write access to push Docker images.
resource "google_project_iam_member" "compute_sa_ar" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

# ─── Service Accounts ────────────────────────────────────────────────────────

resource "google_service_account" "downloader" {
  account_id   = "ofac-downloader"
  display_name = "OFAC SDN Downloader (Cloud Function)"
  project      = var.project_id
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "dataflow_worker" {
  account_id   = "ofac-dataflow"
  display_name = "OFAC SDN Dataflow Worker"
  project      = var.project_id
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "scheduler" {
  account_id   = "ofac-scheduler"
  display_name = "OFAC Scheduler Invoker"
  project      = var.project_id
  depends_on   = [google_project_service.apis]
}

# ─── Cloud Function (Downloader) SA permissions ───────────────────────────────

resource "google_project_iam_member" "downloader_gcs" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.downloader.email}"
}

resource "google_project_iam_member" "downloader_dataflow_developer" {
  project = var.project_id
  role    = "roles/dataflow.developer"
  member  = "serviceAccount:${google_service_account.downloader.email}"
}

# Downloader SA needs to act-as the Dataflow worker SA when submitting jobs
resource "google_service_account_iam_member" "downloader_acts_as_dataflow" {
  service_account_id = google_service_account.dataflow_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.downloader.email}"
}

# ─── Dataflow Worker SA permissions ──────────────────────────────────────────

resource "google_project_iam_member" "dataflow_worker_role" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

# Dataflow workers need to pull the Flex Template Docker image from Artifact Registry
resource "google_project_iam_member" "dataflow_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

resource "google_project_iam_member" "dataflow_gcs" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

resource "google_project_iam_member" "dataflow_bq_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

resource "google_project_iam_member" "dataflow_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

resource "google_project_iam_member" "dataflow_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.dataflow_worker.email}"
}

# ─── Scheduler → Cloud Function invocation ────────────────────────────────────

# Allow scheduler SA to invoke the Cloud Function (Gen2 = Cloud Run underneath)
resource "google_cloudfunctions2_function_iam_member" "scheduler_invoke_cf" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.ofac_downloader.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invoke_run" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.ofac_downloader.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

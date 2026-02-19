resource "google_cloud_scheduler_job" "ofac_weekly" {
  name        = var.scheduler_job_name
  description = "Weekly OFAC SDN ingestion — runs Monday 01:00 SGT (Sunday 17:00 UTC)"
  schedule    = var.scheduler_cron
  time_zone   = "UTC"
  region      = var.region
  project     = var.project_id

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.ofac_downloader.service_config[0].uri
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloudfunctions2_function.ofac_downloader.service_config[0].uri
    }
  }

  retry_config {
    retry_count          = 3
    max_retry_duration   = "3600s"
    min_backoff_duration = "300s"
    max_backoff_duration = "3600s"
    max_doublings        = 2
  }

  attempt_deadline = "600s"

  depends_on = [
    google_project_service.apis,
    google_cloudfunctions2_function.ofac_downloader,
    google_service_account.scheduler,
    google_cloudfunctions2_function_iam_member.scheduler_invoke_cf,
  ]
}

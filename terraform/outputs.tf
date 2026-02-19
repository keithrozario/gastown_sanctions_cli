output "raw_bucket_name" {
  description = "GCS bucket for raw OFAC XML"
  value       = google_storage_bucket.raw_xml.name
}

output "template_bucket_name" {
  description = "GCS bucket for Dataflow templates"
  value       = google_storage_bucket.dataflow.name
}

output "bigquery_dataset" {
  description = "BigQuery dataset ID"
  value       = google_bigquery_dataset.ofac.dataset_id
}

output "bigquery_table" {
  description = "Fully-qualified BigQuery table ID"
  value       = "${var.project_id}.${google_bigquery_dataset.ofac.dataset_id}.${google_bigquery_table.sdn_list.table_id}"
}

output "cloud_function_url" {
  description = "Cloud Function HTTP trigger URL"
  value       = google_cloudfunctions2_function.ofac_downloader.service_config[0].uri
}

output "scheduler_job_name" {
  description = "Cloud Scheduler job name"
  value       = google_cloud_scheduler_job.ofac_weekly.name
}

output "downloader_sa_email" {
  description = "Service account email for the Cloud Function downloader"
  value       = google_service_account.downloader.email
}

output "dataflow_sa_email" {
  description = "Service account email for Dataflow workers"
  value       = google_service_account.dataflow_worker.email
}

output "artifact_registry_repo" {
  description = "Artifact Registry repository URL"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repo_name}"
}

output "dataflow_template_path" {
  description = "GCS path for the Dataflow Flex Template spec"
  value       = "gs://${google_storage_bucket.dataflow.name}/templates/ofac-pipeline.json"
}

output "api_url" {
  description = "OFAC Screening API Cloud Run URL"
  value       = google_cloud_run_v2_service.ofac_api.uri
}

output "lb_ip" {
  description = "Regional external IP address of the load balancer"
  value       = google_compute_address.lb_ip.address
}

output "custom_domain_url" {
  description = "Custom domain URL for the OFAC Screening API"
  value       = "https://${var.subdomain}.${var.base_domain}"
}

resource "google_cloudfunctions2_function" "ofac_downloader" {
  name        = var.cf_function_name
  location    = var.region
  description = "Downloads OFAC SDN Advanced XML and triggers Dataflow ingestion pipeline"
  project     = var.project_id

  build_config {
    runtime     = "python311"
    entry_point = "download_sdn"
    source {
      storage_source {
        bucket = google_storage_bucket.dataflow.name
        object = google_storage_bucket_object.cf_source.name
      }
    }
  }

  service_config {
    max_instance_count             = 1
    min_instance_count             = 0
    available_memory               = "512Mi"
    timeout_seconds                = 540 # 9 minutes (max for HTTP-triggered CF Gen2)
    max_instance_request_concurrency = 1
    service_account_email          = google_service_account.downloader.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = {
      PROJECT_ID        = var.project_id
      REGION            = var.region
      RAW_BUCKET        = google_storage_bucket.raw_xml.name
      TEMPLATE_PATH     = "gs://${google_storage_bucket.dataflow.name}/templates/ofac-pipeline.json"
      STAGING_LOCATION  = "gs://${google_storage_bucket.dataflow.name}/staging"
      TEMP_LOCATION     = "gs://${google_storage_bucket.dataflow.name}/temp"
      DATAFLOW_SA       = google_service_account.dataflow_worker.email
      BQ_PROJECT        = var.project_id
      BQ_DATASET        = var.bq_dataset_id
      BQ_TABLE          = var.bq_table_id
      OFAC_XML_URL         = var.ofac_xml_url
      DATAFLOW_NETWORK     = var.dataflow_network
      DATAFLOW_SUBNETWORK  = var.dataflow_subnetwork
    }
  }

  depends_on = [
    google_project_service.apis,
    google_storage_bucket_object.cf_source,
    google_service_account.downloader,
    google_project_iam_member.downloader_dataflow_developer,
  ]
}

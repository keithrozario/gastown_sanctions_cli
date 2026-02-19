variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "remote-machine-b7af52b6"
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "asia-southeast1"
}

variable "raw_bucket_name" {
  description = "GCS bucket for raw OFAC XML files"
  type        = string
  default     = "ofac-raw-remote-machine-b7af52b6"
}

variable "template_bucket_name" {
  description = "GCS bucket for Dataflow Flex Templates and staging"
  type        = string
  default     = "ofac-dataflow-remote-machine-b7af52b6"
}

variable "bq_dataset_id" {
  description = "BigQuery dataset ID"
  type        = string
  default     = "ofac_sanctions"
}

variable "bq_table_id" {
  description = "BigQuery table ID"
  type        = string
  default     = "sdn_list"
}

variable "artifact_repo_name" {
  description = "Artifact Registry repository name for Dataflow Docker images"
  type        = string
  default     = "ofac-pipeline"
}

variable "cf_function_name" {
  description = "Cloud Function name for the OFAC downloader"
  type        = string
  default     = "ofac-sdn-downloader"
}

variable "scheduler_job_name" {
  description = "Cloud Scheduler job name"
  type        = string
  default     = "ofac-weekly-ingestion"
}

# Monday 01:00 SGT = Sunday 17:00 UTC
variable "scheduler_cron" {
  description = "Cron schedule (UTC) for weekly ingestion"
  type        = string
  default     = "0 17 * * 0"
}

variable "ofac_xml_url" {
  description = "OFAC SDN Advanced XML download URL"
  type        = string
  default     = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML"
}

variable "dataflow_network" {
  description = "VPC network name for Dataflow workers (leave empty to use project default)"
  type        = string
  default     = "remote-machine"
}

variable "dataflow_subnetwork" {
  description = "Full subnetwork URI for Dataflow workers (regions/REGION/subnetworks/SUBNET_NAME)"
  type        = string
  default     = "regions/asia-southeast1/subnetworks/asia-southeast1-remote-machine"
}

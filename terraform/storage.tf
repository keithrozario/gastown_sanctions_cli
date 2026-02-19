# ─── Raw XML Storage Bucket ──────────────────────────────────────────────────

resource "google_storage_bucket" "raw_xml" {
  name          = var.raw_bucket_name
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 90 # Retain 90 days of raw XML files
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Dataflow Templates + Staging Bucket ─────────────────────────────────────

resource "google_storage_bucket" "dataflow" {
  name          = var.template_bucket_name
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 7
      matches_prefix = ["staging/"]
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Cloud Function Source Upload ─────────────────────────────────────────────

data "archive_file" "cf_source" {
  type        = "zip"
  source_dir  = "${path.module}/../cloud_function"
  output_path = "${path.module}/../cloud_function_source.zip"
}

resource "google_storage_bucket_object" "cf_source" {
  name   = "cloud_function/source-${data.archive_file.cf_source.output_md5}.zip"
  bucket = google_storage_bucket.dataflow.name
  source = data.archive_file.cf_source.output_path

  depends_on = [google_storage_bucket.dataflow, data.archive_file.cf_source]
}

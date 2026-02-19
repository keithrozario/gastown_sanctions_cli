resource "google_bigquery_dataset" "ofac" {
  dataset_id    = var.bq_dataset_id
  friendly_name = "OFAC Sanctions List"
  description   = "OFAC SDN Specially Designated Nationals list — weekly ingestion from Advanced XML"
  location      = var.region
  project       = var.project_id

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_table" "sdn_list" {
  dataset_id          = google_bigquery_dataset.ofac.dataset_id
  table_id            = var.bq_table_id
  project             = var.project_id
  description         = "OFAC SDN list — denormalized from Advanced XML with full nested data"
  deletion_protection = false # Allow WRITE_TRUNCATE from Dataflow

  schema = jsonencode([
    {
      name        = "sdn_entry_id"
      type        = "INTEGER"
      mode        = "REQUIRED"
      description = "Unique OFAC ID (DistinctParty FixedRef) — stable across versions"
    },
    {
      name        = "sdn_type"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Entity type: Individual, Entity, Vessel, Aircraft"
    },
    {
      name        = "programs"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Sanctions program tags (e.g. SDGT, IRAN, CUBA)"
    },
    {
      name        = "legal_authorities"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Legal authorities — executive orders and statutes (e.g. E.O. 13224)"
    },
    {
      name        = "primary_name"
      type        = "RECORD"
      mode        = "NULLABLE"
      description = "Primary/canonical identity name"
      fields = [
        {
          name = "full_name"
          type = "STRING"
          mode = "NULLABLE"
        },
        {
          name = "name_parts"
          type = "RECORD"
          mode = "REPEATED"
          fields = [
            { name = "part_type", type = "STRING", mode = "NULLABLE" },
            { name = "part_value", type = "STRING", mode = "NULLABLE" },
            { name = "script", type = "STRING", mode = "NULLABLE" }
          ]
        }
      ]
    },
    {
      name        = "aliases"
      type        = "RECORD"
      mode        = "REPEATED"
      description = "All aliases including AKA, FKA, NKA and non-Latin script names"
      fields = [
        {
          name        = "alias_type"
          type        = "STRING"
          mode        = "NULLABLE"
          description = "Alias type: a.k.a., f.k.a., n.k.a."
        },
        {
          name        = "alias_quality"
          type        = "STRING"
          mode        = "NULLABLE"
          description = "strong or weak"
        },
        {
          name = "full_name"
          type = "STRING"
          mode = "NULLABLE"
        },
        {
          name = "name_parts"
          type = "RECORD"
          mode = "REPEATED"
          fields = [
            { name = "part_type", type = "STRING", mode = "NULLABLE" },
            { name = "part_value", type = "STRING", mode = "NULLABLE" },
            { name = "script", type = "STRING", mode = "NULLABLE" }
          ]
        }
      ]
    },
    {
      name        = "addresses"
      type        = "RECORD"
      mode        = "REPEATED"
      description = "Known addresses"
      fields = [
        { name = "address", type = "STRING", mode = "NULLABLE" },
        { name = "city", type = "STRING", mode = "NULLABLE" },
        { name = "state_province", type = "STRING", mode = "NULLABLE" },
        { name = "postal_code", type = "STRING", mode = "NULLABLE" },
        { name = "country", type = "STRING", mode = "NULLABLE" },
        { name = "region", type = "STRING", mode = "NULLABLE" }
      ]
    },
    {
      name        = "id_documents"
      type        = "RECORD"
      mode        = "REPEATED"
      description = "Identity documents (passports, national IDs, etc.)"
      fields = [
        {
          name        = "id_type"
          type        = "STRING"
          mode        = "NULLABLE"
          description = "Document type: Passport, National ID, etc."
        },
        { name = "id_number", type = "STRING", mode = "NULLABLE" },
        { name = "country", type = "STRING", mode = "NULLABLE" },
        { name = "issue_date", type = "STRING", mode = "NULLABLE" },
        { name = "expiry_date", type = "STRING", mode = "NULLABLE" },
        {
          name        = "is_fraudulent"
          type        = "BOOLEAN"
          mode        = "NULLABLE"
          description = "OFAC-flagged as a fraudulent document"
        }
      ]
    },
    {
      name        = "dates_of_birth"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Known or approximate dates of birth (YYYY, YYYY-MM, or YYYY-MM-DD)"
    },
    {
      name        = "places_of_birth"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Known places of birth"
    },
    {
      name        = "nationalities"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Known nationalities (country names)"
    },
    {
      name        = "citizenships"
      type        = "STRING"
      mode        = "REPEATED"
      description = "Known citizenships (country names)"
    },
    {
      name        = "title"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Title or honorific"
    },
    {
      name        = "gender"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Gender (Male/Female)"
    },
    {
      name        = "remarks"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "OFAC remarks — additional sanctions information"
    },
    {
      name        = "vessel_info"
      type        = "RECORD"
      mode        = "NULLABLE"
      description = "Vessel-specific information (when sdn_type = Vessel)"
      fields = [
        { name = "vessel_type", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_flag", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_owner", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_tonnage", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_grt", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_call_sign", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_mmsi", type = "STRING", mode = "NULLABLE" },
        { name = "vessel_imo", type = "STRING", mode = "NULLABLE" }
      ]
    },
    {
      name        = "aircraft_info"
      type        = "RECORD"
      mode        = "NULLABLE"
      description = "Aircraft-specific information (when sdn_type = Aircraft)"
      fields = [
        { name = "aircraft_type", type = "STRING", mode = "NULLABLE" },
        { name = "aircraft_manufacturer", type = "STRING", mode = "NULLABLE" },
        { name = "aircraft_serial", type = "STRING", mode = "NULLABLE" },
        { name = "aircraft_tail_number", type = "STRING", mode = "NULLABLE" },
        { name = "aircraft_operator", type = "STRING", mode = "NULLABLE" }
      ]
    },
    {
      name        = "additional_sanctions_info"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Additional sanctions information from OFAC feature data"
    },
    {
      name        = "publication_date"
      type        = "DATE"
      mode        = "NULLABLE"
      description = "OFAC list publication date (from XML DateOfIssue)"
    },
    {
      name        = "ingestion_timestamp"
      type        = "TIMESTAMP"
      mode        = "NULLABLE"
      description = "Pipeline ingestion timestamp (UTC)"
    },
    {
      name        = "source_url"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Source URL of the downloaded XML file"
    }
  ])

  depends_on = [google_bigquery_dataset.ofac]
}

# ─── BigQuery Search Index for fuzzy name matching ───────────────────────────
# Created via DDL after table creation (no direct Terraform resource).
# Run this after `terraform apply`:
#
#   bq query --use_legacy_sql=false \
#     "CREATE SEARCH INDEX IF NOT EXISTS sdn_name_index
#      ON \`${project}.${dataset}.${table}\` (primary_name.full_name)"
#
# The deploy.sh script handles this automatically.

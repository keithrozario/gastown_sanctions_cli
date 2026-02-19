# OFAC SDN Sanctions Screening Pipeline

## Overview

A fully serverless, weekly-running ingestion pipeline that downloads the OFAC (Office of Foreign Assets Control) SDN (Specially Designated Nationals) Advanced XML list, parses it into a comprehensive BigQuery table, and enables typo-tolerant fuzzy name matching for compliance screening.

**GCP Project:** `remote-machine-b7af52b6`
**Region:** `asia-southeast1` (Singapore)
**Data source:** [OFAC SDN Advanced XML](https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Cloud Scheduler (weekly, Monday 01:00 SGT)                     │
│  OIDC auth → Cloud Function                                     │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP POST (OIDC token)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Cloud Function Gen2 — ofac-sdn-downloader                      │
│  Runtime: Python 3.11 │ Max: 9 min │ 512MB RAM                 │
│                                                                 │
│  1. GET OFAC SDN XML URL → follows redirect to AWS S3           │
│  2. Upload raw XML → GCS (gs://ofac-raw-.../raw/SDN_YYYYMMDD)  │
│  3. POST to Dataflow REST API → launch Flex Template job        │
└────────────────────────┬────────────────────────────────────────┘
         │ upload        │ launch job
         ▼               ▼
┌──────────────┐  ┌──────────────────────────────────────────────┐
│  GCS Bucket  │  │  Dataflow (Flex Template)                    │
│  ofac-raw-*  │  │  Workers: 1-2 × n1-standard-2               │
│  (90 day     │  │                                              │
│   retention) │  │  1. Read XML from GCS (single element)       │
└──────────────┘  │  2. ParseOfacXmlFn DoFn:                     │
       ▲           │     - Parse ReferenceValueSets (lookups)     │
       │           │     - Parse Locations (geographic data)      │
       └── reads   │     - Parse IDRegDocuments                  │
                   │     - Parse SanctionsEntries (programs)      │
                   │     - Parse DistinctParties (entities)       │
                   │  3. CleanRecordFn DoFn (null cleanup)        │
                   │  4. WriteToBigQuery (WRITE_TRUNCATE)         │
                   └──────────────────┬───────────────────────────┘
                                      │ writes
                                      ▼
                   ┌──────────────────────────────────────────────┐
                   │  BigQuery — ofac_sanctions.sdn_list          │
                   │  ~12,000-14,000 rows, denormalized           │
                   │  Search index on primary_name.full_name      │
                   │                                              │
                   │  Fuzzy queries:                              │
                   │    EDIT_DISTANCE() — typo tolerance          │
                   │    SOUNDEX()       — phonetic matching       │
                   │    SEARCH()        — full-text index         │
                   └──────────────────────────────────────────────┘
```

---

## Data Model

### OFAC Advanced XML Format

The OFAC SDN Advanced XML uses a **highly normalized** structure conforming to the UN/Wolfsberg Group international sanctions standard. Key sections:

| Section | Description |
|---------|-------------|
| `ReferenceValueSets` | Enumeration lookups (alias types, feature types, countries, name part types, etc.) referenced by numeric ID throughout the document |
| `Locations` | Geographic entities (address, city, state, postal code, country) referenced by `LocationID` |
| `IDRegDocuments` | Identity documents (passports, national IDs) referenced by `DocumentID` |
| `DistinctParties` | The sanctioned entities — individuals, companies, vessels, aircraft |
| `SanctionsEntries` | Which sanctions programs and legal authorities apply to each entity |
| `ProfileRelationships` | Relationships between entities (beneficial ownership, associations) |

The Dataflow pipeline **resolves all numeric ID references** into human-readable values at parse time.

### BigQuery Table: `ofac_sanctions.sdn_list`

One row per OFAC `DistinctParty`. All data from the XML is captured using `ARRAY` and `STRUCT` (nested/repeated) fields.

| Field | Type | Description |
|-------|------|-------------|
| `sdn_entry_id` | INTEGER REQUIRED | OFAC FixedRef — unique, stable across list versions |
| `sdn_type` | STRING | Individual, Entity, Vessel, or Aircraft |
| `programs` | ARRAY\<STRING\> | Sanctions programs (SDGT, IRAN, CUBA, RUSSIA, etc.) |
| `legal_authorities` | ARRAY\<STRING\> | Executive orders and statutes (E.O. 13224, etc.) |
| `primary_name` | STRUCT | Primary/canonical name with typed name parts |
| `aliases` | ARRAY\<STRUCT\> | All aliases (AKA/FKA/NKA) including non-Latin scripts |
| `addresses` | ARRAY\<STRUCT\> | Known addresses (address, city, state, postal, country) |
| `id_documents` | ARRAY\<STRUCT\> | Identity documents (type, number, country, dates) |
| `dates_of_birth` | ARRAY\<STRING\> | Known/approximate dates (YYYY, YYYY-MM, YYYY-MM-DD) |
| `places_of_birth` | ARRAY\<STRING\> | Known places of birth |
| `nationalities` | ARRAY\<STRING\> | Known nationalities |
| `citizenships` | ARRAY\<STRING\> | Known citizenships |
| `title` | STRING | Title or honorific |
| `gender` | STRING | Male/Female |
| `remarks` | STRING | OFAC remarks and additional sanctions information |
| `vessel_info` | STRUCT | Vessel data (type, flag, owner, tonnage, MMSI, IMO) |
| `aircraft_info` | STRUCT | Aircraft data (type, serial, manufacturer, tail number) |
| `publication_date` | DATE | OFAC list publication date |
| `ingestion_timestamp` | TIMESTAMP | Pipeline run timestamp |
| `source_url` | STRING | Download URL |

---

## Components

### 1. Cloud Function (`cloud_function/`)

**File:** `cloud_function/main.py`

- **Runtime:** Python 3.11 (Cloud Functions Gen2)
- **Trigger:** HTTP (invoked by Cloud Scheduler via OIDC-authenticated POST)
- **Timeout:** 9 minutes (540 seconds)
- **Memory:** 512 MiB

**Logic:**
1. Downloads OFAC SDN Advanced XML via HTTPS, following the redirect to the S3 pre-signed URL
2. Uploads raw XML to `gs://ofac-raw-{project}/raw/SDN_ADVANCED_{YYYYMMDD}.XML`
3. Calls the Dataflow REST API to launch a Flex Template job with the GCS path and BigQuery destination as parameters

### 2. Dataflow Pipeline (`dataflow/`)

**Files:**
- `pipeline.py` — Apache Beam pipeline entry point
- `xml_parser.py` — Full OFAC Advanced XML parsing logic
- `Dockerfile` — Flex Template container (extends `apache/beam_python3.11_sdk`)
- `metadata.json` — Flex Template parameter definitions
- `requirements.txt` — `apache-beam[gcp]`, `google-cloud-storage`

**Pipeline steps:**
```
Create([gcs_uri])
  → ParseOfacXmlFn          # Downloads XML, parses into row dicts
  → CleanRecordFn           # Strips empty nested structs
  → WriteToBigQuery         # WRITE_TRUNCATE → full refresh
```

**XML Parser (`xml_parser.py`) — Two-pass approach:**

Pass 1 — Build lookup maps:
- `ReferenceValueSets` → alias types, party subtypes, feature types, country names, script names, name part types, legal bases, sanctions programs, location part types, ID document types
- `Locations` → `{location_id: {address, city, state_province, postal_code, country}}`
- `IDRegDocuments` → `{doc_id: {id_type, id_number, country, issue_date, expiry_date}}`
- `SanctionsEntries` → `{profile_id: {programs, legal_authorities, remarks}}`

Pass 2 — Emit rows:
For each `DistinctParty`:
- Resolve `PartySubTypeID` → entity type (Individual/Entity/Vessel/Aircraft)
- Join with `sanctions_map[profile_id]` for programs/legal authorities
- Parse `Identity` → primary name + aliases (with name part type resolution via `NamePartGroups`)
- Parse `Feature` elements → dates of birth, places of birth, nationalities, citizenships, addresses (via `LocationID` resolution), ID documents (via `IDRegDocumentReference`), vessel/aircraft features, gender, title

### 3. Screening API (`api/`)

**Files:**
- `main.py` — FastAPI application with lifespan-managed BigQuery client
- `queries.py` — `screen_names()` and `get_entry()` functions; parameterised SQL
- `models.py` — Pydantic response models
- `Dockerfile` — `python:3.11-slim`; uvicorn on port 8080
- `deploy.sh` — Cloud Build → terraform apply → unit + integration tests
- `tests/test_unit.py` — 7 offline tests (mocked BQ client via `unittest.mock`)
- `tests/test_integration.py` — 5 live tests (reads `CLOUD_RUN_URL` from env)

**Endpoints:**
- `GET /health` — service liveness + table reference
- `GET /screen?name=&threshold=&limit=` — fuzzy name screening (tiered scoring)
- `GET /entry/{sdn_entry_id}` — full BQ row by OFAC FixedRef ID

**Screening query design:**

The query unnests each SDN entry into one row per name (primary + aliases), then scores
against the query name simultaneously across three mechanisms:

```sql
CASE
  WHEN LOWER(all_name) = LOWER(@name)                    THEN 1  -- exact
  WHEN EDIT_DISTANCE(...) <= 2                            THEN 2  -- tight fuzzy
  WHEN EDIT_DISTANCE(...) <= @threshold                   THEN 3  -- loose fuzzy
  WHEN SOUNDEX(all_name) = SOUNDEX(@name)                THEN 4  -- phonetic
END AS match_score
```

All query parameters (`@name`, `@threshold`, `@limit`) are passed as BigQuery
`ScalarQueryParameter` objects — never interpolated into SQL — preventing injection.

**Infrastructure:**
- Deployed as Cloud Run v2 service `ofac-screening-api` (public, `INGRESS_TRAFFIC_ALL`)
- Service account `ofac-api` with `bigquery.dataViewer` + `bigquery.jobUser` + `aiplatform.user`
- `BQ_TABLE`, `BQ_PROJECT`, `VERTEX_REGION`, `VERTEX_MODEL` injected as env vars at deploy time

### 4b. Document Screening (`POST /screen/document`)

Accepts a free-text document, extracts named entities using Vertex AI Gemini, and screens each entity against the SDN table.

**Entity extraction (`api/vertex.py`):**
- Uses `google-cloud-aiplatform` SDK with `response_mime_type="application/json"` and a JSON Schema response schema to guarantee structured output
- Model: `gemini-2.0-flash-001` via `VERTEX_MODEL` env var (default: `gemini-2.0-flash-001`)
- Region: `us-central1` via `VERTEX_REGION` env var (`gemini-2.0-flash-001` not yet available in `asia-southeast1`)
- Entity types extracted: `person`, `organization`, `vessel`, `aircraft`

**Request flow:**
1. `extract_entities(text)` → list of `{name, entity_type}` dicts
2. Each entity screened via `screen_names()` (same BQ function as `/screen`)
3. Response includes per-entity hit list, overall `document_clear` flag, and counts

### 5. Terraform IaC (`terraform/`)

| File | Resources |
|------|-----------|
| `main.tf` | Terraform + provider configuration |
| `variables.tf` | Input variables (project, region, bucket names, etc.) |
| `outputs.tf` | Outputs (bucket names, function URL, BQ table, API URL) |
| `apis.tf` | `google_project_service` — enables 15 GCP APIs |
| `iam.tf` | Service accounts + IAM for ingestion pipeline |
| `api_iam.tf` | `ofac-api` SA + `bigquery.dataViewer` + `bigquery.jobUser` + `aiplatform.user` |
| `storage.tf` | 2 GCS buckets + Cloud Function source ZIP upload |
| `bigquery.tf` | BigQuery dataset + table with full schema |
| `artifact.tf` | Artifact Registry Docker repository |
| `cloudfunction.tf` | Cloud Functions Gen2 deployment |
| `cloudrun.tf` | Cloud Run v2 service (screening API) + public invoker |
| `scheduler.tf` | Cloud Scheduler job (weekly cron) |

### 6. Service Accounts

| SA | Purpose | Permissions |
|----|---------|-------------|
| `ofac-downloader` | Cloud Function identity | storage.objectAdmin, dataflow.developer, iam.serviceAccountUser (for dataflow SA) |
| `ofac-dataflow` | Dataflow worker identity | dataflow.worker, storage.objectAdmin, bigquery.dataEditor, bigquery.jobUser, logging.logWriter |
| `ofac-scheduler` | Cloud Scheduler invoker | cloudfunctions.invoker, run.invoker |
| `ofac-api` | Cloud Run (screening API) | bigquery.dataViewer, bigquery.jobUser, aiplatform.user |

---

## Fuzzy Name Matching

Three complementary techniques are available in BigQuery for typo-tolerant name search:

### 1. Edit Distance (`EDIT_DISTANCE`)

Counts the minimum number of character edits (insertions, deletions, substitutions) to transform one string into another (Levenshtein distance).

```sql
SELECT * FROM `ofac_sanctions.sdn_list`
WHERE EDIT_DISTANCE(LOWER(primary_name.full_name), LOWER('Sadam Husain')) <= 3
```

**Thresholds:**
- `<= 1`: Very tight — one typo (transposition or swap)
- `<= 2`: Moderate — common misspellings
- `<= 3`: Loose — significant variation; expect some false positives

### 2. Phonetic Matching (`SOUNDEX`)

Converts names to a phonetic code based on how they sound, catching misspellings that preserve pronunciation.

```sql
SELECT * FROM `ofac_sanctions.sdn_list`
WHERE SOUNDEX(primary_name.full_name) = SOUNDEX('Kadafi')
```

Best for names with multiple common transliterations (Gaddafi/Qaddafi/Khadhafi).

### 3. Full-Text Search Index (`SEARCH`)

Requires a BigQuery search index on `primary_name.full_name`. Supports fast keyword-based lookup.

```sql
-- Create index (done by deploy.sh):
CREATE SEARCH INDEX sdn_name_index ON `ofac_sanctions.sdn_list` (primary_name.full_name);

-- Query:
SELECT * FROM `ofac_sanctions.sdn_list`
WHERE SEARCH(primary_name.full_name, 'hussein')
```

### Combined Scoring Query

`queries/test_queries.sql` includes a ranked combined query (Query 3e) that:
- Searches both primary name AND all aliases
- Assigns match scores (1=exact, 2=edit dist ≤2, 3=edit dist ≤4, 4=soundex, 5=keyword)
- Returns ranked results for compliance review

---

## Deployment

### Prerequisites

```bash
# Required tools
terraform --version     # >= 1.5
gcloud --version        # >= 500.0
docker --version        # OR set USE_CLOUD_BUILD=true
bq --version

# Authentication (already configured on this machine)
gcloud auth list
gcloud config get-value project
```

### Deploy

```bash
cd /path/to/sanctions_screener/mayor/rig

# Full deployment (builds image, creates all GCP resources)
./deploy.sh

# Or use Cloud Build instead of local Docker:
USE_CLOUD_BUILD=true ./deploy.sh
```

### Deploy sequence — ingestion pipeline (`deploy.sh`)

1. `terraform apply -target=google_project_service.apis` — Enable APIs
2. Wait 60 seconds for API propagation
3. `terraform apply -target=google_artifact_registry_repository.ofac` — Create registry
4. Build & push Dataflow Docker image to Artifact Registry
5. `gcloud dataflow flex-template build` — Upload Flex Template spec to GCS
6. `terraform apply` — Deploy Cloud Function, Scheduler, BigQuery, buckets, IAM
7. `bq query "CREATE SEARCH INDEX ..."` — Create BigQuery search index

### Deploy sequence — screening API (`api/deploy.sh`)

1. `gcloud builds submit` — Build & push `ofac-api:latest` to Artifact Registry
2. `terraform apply` — Create `ofac-api` SA, IAM bindings, Cloud Run service
3. `pytest tests/test_unit.py` — Run offline unit tests (mocked BQ)
4. `terraform output -raw api_url` → `CLOUD_RUN_URL`
5. `pytest tests/test_integration.py` — Run live integration tests

### Trigger a manual run

```bash
gcloud scheduler jobs run ofac-weekly-ingestion \
  --location=asia-southeast1 \
  --project=remote-machine-b7af52b6
```

### Monitor

```bash
# Cloud Function logs
gcloud functions logs read ofac-sdn-downloader \
  --gen2 --region=asia-southeast1 --limit=50

# Dataflow jobs
gcloud dataflow jobs list \
  --region=asia-southeast1 --project=remote-machine-b7af52b6

# BigQuery row count
bq query --use_legacy_sql=false \
  'SELECT COUNT(*) FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`'
```

---

## Testing

### API unit tests (offline, no GCP)

```bash
cd api && .venv/bin/pytest tests/test_unit.py -v
```

12 cases: health; screen happy path, zero threshold, missing-name 422, limit;
document screen no-hit, SDN hit, multi-entity, no entities, empty-text 422;
entry found, entry 404.

### API integration tests (live Cloud Run)

```bash
export CLOUD_RUN_URL=$(cd terraform && terraform output -raw api_url)
cd api && .venv/bin/pytest tests/test_integration.py -v
```

8 cases: health; exact SDN name hit (SDGT program), fuzzy Saddam Hussein,
missing-name 422; document with "USAMA BIN LADIN" → match, document with
fictional names → valid structure; entry by FixedRef ID, entry 0 → 404.

### BigQuery validation queries

```bash
bq query --use_legacy_sql=false < queries/test_queries.sql
```

### Expected data results

| Test | Expected |
|------|----------|
| Total row count | ~12,000–18,000 |
| Records with primary name | ~12,000–18,000 |
| Records with programs | ~12,000–18,000 |
| Vessel records | ~300–500 |
| Records with aliases | ~8,000–12,000 |
| Records with ID documents | ~5,000–8,000 |

### Fuzzy search validation examples

| Query | Expected top hit |
|-------|-----------------|
| `Sadam Husain` (edit dist ≤ 4) | `SADDAM HUSSEIN` |
| `Osama Bin Laden` (edit dist ≤ 4) | `USAMA BIN LADIN` |
| `Kadafi` (SOUNDEX) | `MUAMMAR QADHAFI` / similar |
| `hussein` (SEARCH) | Multiple entries with "HUSSEIN" |

---

## Schedule

The pipeline runs **weekly on Mondays at 01:00 SGT (Sunday 17:00 UTC)**.

OFAC typically publishes SDN list updates on business days. The weekly Monday run ensures the dataset reflects the most recent Friday update.

Cron: `0 17 * * 0` (UTC)

---

## Cost Estimate

| Service | Estimated monthly cost |
|---------|----------------------|
| Cloud Scheduler | ~$0.10/month |
| Cloud Function Gen2 | ~$0.01/month (4 invocations × 9 min × 512MB) |
| Dataflow | ~$2–5/run × 4 runs = ~$8–20/month |
| BigQuery storage | ~$0.02/month (~50MB table) |
| BigQuery queries | Pay-per-query (100MB per screen call) |
| GCS | ~$0.01/month (XML files, 90-day retention) |
| Artifact Registry | ~$0.05/month (image storage) |
| Cloud Run (screening API) | ~$0/month at low traffic (scales to zero) |
| **Total** | **~$10–25/month** |

---

## File Structure

```
sanctions_screener/mayor/rig/
├── README.md               ← Quick-start and repo overview
├── IMPLEMENTATION.md       ← This file — full architecture + design
├── deploy.sh               ← Deploy ingestion pipeline
│
├── api/                    ← Cloud Run screening API
│   ├── README.md           ← Endpoint documentation
│   ├── main.py             ← FastAPI app (lifespan BQ client, routes)
│   ├── queries.py          ← screen_names() + get_entry() BQ functions
│   ├── models.py           ← Pydantic response models
│   ├── requirements.txt    ← Python dependencies
│   ├── Dockerfile          ← python:3.11-slim, uvicorn :8080
│   ├── deploy.sh           ← Build → terraform → unit + integration tests
│   └── tests/
│       ├── test_unit.py        ← 7 offline tests (mocked BQ)
│       └── test_integration.py ← 5 live tests (CLOUD_RUN_URL env var)
│
├── terraform/
│   ├── main.tf             ← Provider configuration
│   ├── variables.tf        ← Input variables
│   ├── outputs.tf          ← Outputs (bucket names, function URL, api_url)
│   ├── apis.tf             ← GCP API enablement
│   ├── iam.tf              ← Service accounts and IAM (ingestion pipeline)
│   ├── api_iam.tf          ← ofac-api SA + BQ IAM (screening API)
│   ├── storage.tf          ← GCS buckets + CF source upload
│   ├── bigquery.tf         ← BQ dataset, table, schema
│   ├── artifact.tf         ← Artifact Registry repository
│   ├── cloudfunction.tf    ← Cloud Functions Gen2 deployment
│   ├── cloudrun.tf         ← Cloud Run v2 screening API service
│   └── scheduler.tf        ← Cloud Scheduler weekly job
│
├── dataflow/
│   ├── pipeline.py         ← Apache Beam pipeline entry point
│   ├── xml_parser.py       ← OFAC Advanced XML parsing logic
│   ├── Dockerfile          ← Flex Template container image
│   ├── requirements.txt    ← Python dependencies
│   └── metadata.json       ← Flex Template parameter definitions
│
├── cloud_function/
│   ├── main.py             ← Downloader + Dataflow launcher
│   └── requirements.txt    ← Python dependencies
│
└── queries/
    └── test_queries.sql    ← Validation and fuzzy search queries
```

---

## References

- [OFAC SDN Advanced XML FAQ](https://ofac.treasury.gov/sdn-list-data-formats-data-schemas/frequently-asked-questions-on-advanced-sanctions-list-standard)
- [OFAC Advanced XSD Schema](https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML.xsd)
- [BigQuery EDIT_DISTANCE](https://cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#edit_distance)
- [BigQuery SOUNDEX](https://cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#soundex)
- [BigQuery Search Indexes](https://cloud.google.com/bigquery/docs/search-index)
- [Dataflow Flex Templates](https://cloud.google.com/dataflow/docs/guides/templates/using-flex-templates)
- [Cloud Functions Gen2](https://cloud.google.com/functions/docs/concepts/version-comparison)
- [Cloud Run v2 Terraform](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_service)
- [FastAPI](https://fastapi.tiangolo.com/)
- [google-cloud-bigquery Python client](https://cloud.google.com/python/docs/reference/bigquery/latest)

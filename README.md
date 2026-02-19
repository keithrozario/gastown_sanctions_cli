# OFAC SDN Sanctions Screening Pipeline

A fully serverless OFAC (Office of Foreign Assets Control) SDN (Specially Designated
Nationals) pipeline on GCP. It ingests the OFAC Advanced XML list weekly into BigQuery
and exposes a fuzzy-screening HTTP API on Cloud Run.

**GCP Project:** `remote-machine-b7af52b6`
**Region:** `asia-southeast1` (Singapore)
**Data source:** [OFAC SDN Advanced XML](https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML)

---

## Architecture

```
Cloud Scheduler (weekly, Mon 01:00 SGT)
  │ OIDC POST
  ▼
Cloud Function Gen2 — ofac-sdn-downloader
  │ upload XML          │ launch Dataflow job
  ▼                     ▼
GCS (ofac-raw-*)    Dataflow Flex Template
                        │ parse + write
                        ▼
                    BigQuery — ofac_sanctions.sdn_list
                        │ SQL queries (EDIT_DISTANCE, SOUNDEX)
                        ▼
                    Cloud Run — ofac-screening-api  ← HTTP API
                        │ POST /screen/document
                        ▼
                    Vertex AI — Gemini (entity extraction)
```

Full architecture and data model: see **[IMPLEMENTATION.md](IMPLEMENTATION.md)**

---

## API Quick Start

The screening API is publicly available:

```bash
# Health check
curl "$CLOUD_RUN_URL/health"

# Fuzzy screen a single name (edit distance + SOUNDEX, ranked by confidence)
curl "$CLOUD_RUN_URL/screen?name=Osama+Bin+Laden"

# Screen a free-text document — extracts entities via Gemini, screens each against SDN
curl -X POST "$CLOUD_RUN_URL/screen/document" \
  -H "Content-Type: application/json" \
  -d '{"text": "Wire from USAMA BIN LADIN received."}'

# Exact entity lookup by OFAC FixedRef ID
curl "$CLOUD_RUN_URL/entry/7771"
```

See **[api/README.md](api/README.md)** for full endpoint documentation.

---

## Repository Layout

```
rig/
├── README.md               ← You are here
├── IMPLEMENTATION.md       ← Full architecture, data model, design decisions
├── deploy.sh               ← Deploy ingestion pipeline (Terraform + Dataflow)
│
├── api/                    ← Cloud Run screening API
│   ├── README.md           ← API endpoint documentation
│   ├── main.py             ← FastAPI app
│   ├── queries.py          ← BigQuery fuzzy-screening queries
│   ├── models.py           ← Pydantic request/response models
│   ├── vertex.py           ← Vertex AI Gemini entity extraction
│   ├── requirements.txt    ← Python dependencies
│   ├── Dockerfile          ← Container definition
│   ├── deploy.sh           ← Build image → terraform → test
│   └── tests/
│       ├── test_unit.py        ← Offline tests (mocked BQ)
│       └── test_integration.py ← Live tests (requires CLOUD_RUN_URL)
│
├── terraform/              ← Infrastructure as Code
│   ├── main.tf             ← Provider config
│   ├── variables.tf        ← Input variables
│   ├── outputs.tf          ← Outputs (URLs, bucket names, etc.)
│   ├── apis.tf             ← GCP API enablement
│   ├── iam.tf              ← Service accounts + IAM (ingestion pipeline)
│   ├── api_iam.tf          ← Service account + IAM (screening API)
│   ├── storage.tf          ← GCS buckets
│   ├── bigquery.tf         ← BQ dataset + table schema
│   ├── artifact.tf         ← Artifact Registry Docker repo
│   ├── cloudfunction.tf    ← Cloud Function (downloader)
│   ├── cloudrun.tf         ← Cloud Run (screening API)
│   └── scheduler.tf        ← Cloud Scheduler (weekly trigger)
│
├── dataflow/               ← Apache Beam ingestion pipeline
│   ├── pipeline.py         ← Entry point
│   ├── xml_parser.py       ← OFAC Advanced XML → BQ row parser
│   ├── Dockerfile          ← Flex Template container
│   ├── metadata.json       ← Flex Template parameter definitions
│   └── requirements.txt
│
├── cloud_function/         ← Downloader (HTTP trigger → Dataflow)
│   ├── main.py
│   └── requirements.txt
│
└── queries/
    └── test_queries.sql    ← Validation + fuzzy search examples
```

---

## Deployment

### Full pipeline (ingestion + API)

```bash
# Deploy ingestion infrastructure (Dataflow, Cloud Function, BigQuery, etc.)
./deploy.sh

# Deploy the screening API
cd api && ./deploy.sh
```

### Trigger a manual ingestion run

```bash
gcloud scheduler jobs run ofac-weekly-ingestion \
  --location=asia-southeast1 --project=remote-machine-b7af52b6
```

### Run tests

```bash
# Unit tests (no GCP required)
cd api && .venv/bin/pytest tests/test_unit.py -v

# Integration tests (requires deployed API)
export CLOUD_RUN_URL=$(cd terraform && terraform output -raw api_url)
cd api && .venv/bin/pytest tests/test_integration.py -v
```

---

## References

- [OFAC SDN Advanced XML FAQ](https://ofac.treasury.gov/sdn-list-data-formats-data-schemas/frequently-asked-questions-on-advanced-sanctions-list-standard)
- [BigQuery EDIT_DISTANCE](https://cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#edit_distance)
- [BigQuery SOUNDEX](https://cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#soundex)
- [Dataflow Flex Templates](https://cloud.google.com/dataflow/docs/guides/templates/using-flex-templates)

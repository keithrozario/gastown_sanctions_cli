# OFAC Screening API

A FastAPI service deployed on Cloud Run that exposes fuzzy sanctions screening over HTTP.
Backed by the BigQuery `ofac_sanctions.sdn_list` table with ~18,000 SDN entries.

---

## Endpoints

### `GET /health`

Returns service status and the backing BQ table name.

```bash
curl "$CLOUD_RUN_URL/health"
```

```json
{"status": "ok", "table": "remote-machine-b7af52b6.ofac_sanctions.sdn_list"}
```

---

### `GET /screen`

Fuzzy-screens a name against all SDN entries (primary names + aliases).

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | yes | — | Name to screen |
| `threshold` | integer 0–10 | no | `4` | Max edit distance (Levenshtein) to include as a match |
| `limit` | integer 1–100 | no | `20` | Maximum results returned |

**Match scoring (tiered, lower = higher confidence):**

| Score | Condition |
|-------|-----------|
| 1 | Exact match (case-insensitive) |
| 2 | Edit distance ≤ 2 |
| 3 | Edit distance ≤ `threshold` |
| 4 | SOUNDEX phonetic match |

Results are ordered by `match_score` then `edit_distance`.

**Example:**

```bash
curl "$CLOUD_RUN_URL/screen?name=Osama+Bin+Laden&threshold=4&limit=10"
```

```json
{
  "query": "Osama Bin Laden",
  "threshold": 4,
  "total_hits": 1,
  "results": [
    {
      "sdn_entry_id": 7771,
      "sdn_type": "Individual",
      "primary_name": "BIN LADIN Usama bin Muhammad bin Awad",
      "matched_name": "BIN LADIN Usama bin Muhammad bin Awad",
      "match_score": 3,
      "edit_distance": 4,
      "programs": ["SDGT"],
      "legal_authorities": [],
      "dates_of_birth": ["1957-07-30"],
      "nationalities": ["Saudi Arabia"]
    }
  ]
}
```

**Error responses:**
- `422 Unprocessable Entity` — `name` parameter missing or invalid

---

### `POST /screen/document`

Extracts named entities from a plain-text document using Vertex AI (Gemini),
then screens each entity against the OFAC SDN table.

**Request body (JSON):**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | yes | — | Plain-text document to screen |
| `threshold` | integer 0–10 | no | `4` | Max edit distance per entity screen |
| `limit_per_entity` | integer 1–20 | no | `5` | Max SDN hits returned per entity |

**Example:**

```bash
curl -X POST "$CLOUD_RUN_URL/screen/document" \
  -H "Content-Type: application/json" \
  -d '{"text": "Wire from USAMA BIN LADIN received."}'
```

```json
{
  "entities_extracted": [{"name": "USAMA BIN LADIN", "entity_type": "person"}],
  "screening_results": [{
    "entity": "USAMA BIN LADIN",
    "entity_type": "person",
    "is_match": true,
    "hits": [{"sdn_entry_id": 7771, "match_score": 1, ...}]
  }],
  "document_clear": false,
  "total_entities_extracted": 1,
  "total_matches": 1
}
```

**Error responses:**
- `422 Unprocessable Entity` — `text` field missing or empty

---

### `GET /entry/{sdn_entry_id}`

Returns the full BigQuery row for a single entity by OFAC FixedRef ID.

```bash
curl "$CLOUD_RUN_URL/entry/7771"
```

Returns the complete BQ row (all columns including addresses, id\_documents, aliases,
vessel\_info, aircraft\_info, etc.).

**Error responses:**
- `404 Not Found` — no entry with that `sdn_entry_id`

---

## Matching Strategy

The screening query (`queries.py`) runs against an expanded name table:
each SDN entry is unnested into one row per name (primary name + all aliases),
then scored across three mechanisms simultaneously:

```
exact match           → score 1  (highest confidence)
edit distance ≤ 2     → score 2
edit distance ≤ N     → score 3  (N = threshold parameter)
SOUNDEX phonetic      → score 4
```

The SOUNDEX tier catches phonetic variants that edit distance misses
(e.g. "Kadafi" → "QADHAFI"). Both mechanisms run in a single BQ query.

---

## Local Development

```bash
cd api/

# Create venv + install deps
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

# Run unit tests (no GCP needed)
.venv/bin/pytest tests/test_unit.py -v

# Run the server locally (needs ADC credentials + BQ access)
BQ_TABLE=remote-machine-b7af52b6.ofac_sanctions.sdn_list \
  .venv/bin/uvicorn main:app --reload
```

## Deployment

```bash
cd api/
./deploy.sh    # Cloud Build → terraform apply → unit + integration tests
```

Or step by step:

```bash
# 1. Build and push image
gcloud builds submit . \
  --tag=asia-southeast1-docker.pkg.dev/remote-machine-b7af52b6/ofac-pipeline/ofac-api:latest

# 2. Deploy infrastructure (SA, IAM, Cloud Run service)
cd ../terraform && terraform apply -auto-approve

# 3. Run integration tests against live URL
export CLOUD_RUN_URL=$(terraform output -raw api_url)
cd .. && .venv/bin/pytest api/tests/test_integration.py -v
```

---

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app: lifespan BQ client, route handlers |
| `queries.py` | `screen_names()` and `get_entry()` — parameterised BQ queries |
| `models.py` | Pydantic response models (`HealthResponse`, `ScreenResponse`, `ScreenResult`, document screen models) |
| `vertex.py` | Vertex AI Gemini entity extraction (`extract_entities()`) |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | `python:3.11-slim` → uvicorn on port 8080 |
| `deploy.sh` | Full deploy + test automation |
| `tests/test_unit.py` | 12 offline tests with mocked BQ/Vertex clients |
| `tests/test_integration.py` | 7 live tests against deployed URL |

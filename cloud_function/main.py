"""
OFAC SDN Downloader — Cloud Function (Gen2)

Triggered weekly by Cloud Scheduler.
1. Downloads the OFAC SDN Advanced XML (follows HTTPS redirect to S3 pre-signed URL)
2. Uploads raw XML to GCS with a date-stamped filename
3. Launches a Dataflow Flex Template job to parse and load data into BigQuery

Environment variables (set by Terraform):
  PROJECT_ID       - GCP project ID
  REGION           - GCP region (e.g. asia-southeast1)
  RAW_BUCKET       - GCS bucket for raw XML files
  TEMPLATE_PATH    - GCS path to the Dataflow Flex Template JSON spec
  STAGING_LOCATION - GCS path for Dataflow staging
  TEMP_LOCATION    - GCS path for Dataflow temp files
  DATAFLOW_SA      - Service account email for Dataflow workers
  BQ_PROJECT       - BigQuery project ID
  BQ_DATASET       - BigQuery dataset ID
  BQ_TABLE         - BigQuery table ID
  OFAC_XML_URL     - OFAC SDN Advanced XML download URL
"""

import functions_framework
import json
import logging
import os
import re
from datetime import datetime, timezone

import requests
from google.cloud import storage as gcs
import google.auth
import google.auth.transport.requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Configuration from environment (read lazily inside handlers) ──────────────
# Use get() with empty string defaults so module import succeeds even without env vars.
# The handler validates required vars at runtime.
def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


OFAC_XML_URL_DEFAULT = (
    "https://sanctionslistservice.ofac.treas.gov"
    "/api/PublicationPreview/exports/SDN_ADVANCED.XML"
)

DOWNLOAD_TIMEOUT_SECONDS = 300  # 5 minutes for download
REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OFAC-Sanctions-Screener/1.0; "
        "GCP-Cloud-Function; +https://github.com)"
    ),
    "Accept": "text/xml,application/xml,*/*",
}


@functions_framework.http
def download_sdn(request):
    """
    Main entry point for the Cloud Function.
    Downloads the OFAC SDN XML, stores it in GCS, and triggers Dataflow.
    """
    logger.info("OFAC SDN downloader triggered")

    # Read config at request time (env vars are set by Terraform)
    project_id = _cfg("PROJECT_ID")
    region = _cfg("REGION")
    raw_bucket = _cfg("RAW_BUCKET")
    template_path = _cfg("TEMPLATE_PATH")
    staging_location = _cfg("STAGING_LOCATION")
    temp_location = _cfg("TEMP_LOCATION")
    dataflow_sa = _cfg("DATAFLOW_SA")
    bq_project = _cfg("BQ_PROJECT") or project_id
    bq_dataset = _cfg("BQ_DATASET")
    bq_table = _cfg("BQ_TABLE")
    ofac_xml_url = _cfg("OFAC_XML_URL") or OFAC_XML_URL_DEFAULT
    dataflow_network = _cfg("DATAFLOW_NETWORK")
    dataflow_subnetwork = _cfg("DATAFLOW_SUBNETWORK")

    # Validate required config
    missing = [k for k, v in {
        "PROJECT_ID": project_id, "REGION": region, "RAW_BUCKET": raw_bucket,
        "TEMPLATE_PATH": template_path, "DATAFLOW_SA": dataflow_sa,
        "BQ_DATASET": bq_dataset, "BQ_TABLE": bq_table,
    }.items() if not v]
    if missing:
        msg = f"Missing required environment variables: {missing}"
        logger.error(msg)
        return json.dumps({"status": "error", "error": msg}), 500, {"Content-Type": "application/json"}

    try:
        # Step 1: Download the XML
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        gcs_blob_name = f"raw/SDN_ADVANCED_{date_str}.XML"

        xml_bytes = _download_ofac_xml(ofac_xml_url)
        logger.info("Downloaded %d bytes from OFAC", len(xml_bytes))

        # Step 2: Upload to GCS
        gcs_uri = _upload_to_gcs(xml_bytes, gcs_blob_name, raw_bucket, project_id)
        logger.info("Uploaded to %s", gcs_uri)

        # Step 3: Launch Dataflow job
        job_name = f"ofac-sdn-ingest-{date_str}"
        bq_table_ref = f"{bq_project}:{bq_dataset}.{bq_table}"
        job_id = _launch_dataflow_job(
            job_name, gcs_uri, bq_table_ref,
            project_id, region, template_path,
            staging_location, temp_location, dataflow_sa,
            dataflow_network, dataflow_subnetwork,
        )
        logger.info("Launched Dataflow job: %s", job_id)

        response = {
            "status": "success",
            "date": date_str,
            "gcs_path": gcs_uri,
            "xml_size_bytes": len(xml_bytes),
            "dataflow_job_id": job_id,
            "bq_table": bq_table_ref,
        }
        return json.dumps(response), 200, {"Content-Type": "application/json"}

    except Exception as exc:
        logger.exception("Download pipeline failed: %s", exc)
        return (
            json.dumps({"status": "error", "error": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )


def _download_ofac_xml(url: str) -> bytes:
    """
    Download the OFAC SDN Advanced XML.
    Handles the redirect from OFAC's API endpoint to the AWS S3 pre-signed URL.
    """
    logger.info("Downloading from %s", url)

    session = requests.Session()
    response = session.get(
        url,
        headers=REQUESTS_HEADERS,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        stream=True,
        allow_redirects=True,  # Follow the redirect to S3
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "xml" not in content_type.lower() and "octet-stream" not in content_type.lower():
        logger.warning("Unexpected content type: %s", content_type)

    xml_bytes = response.content
    if not xml_bytes:
        raise ValueError("Empty response from OFAC server")

    # Sanity check: should start with XML declaration or root element
    header = xml_bytes[:200].decode("utf-8", errors="replace")
    if "<" not in header:
        raise ValueError(f"Response does not appear to be XML. Header: {header!r}")

    logger.info(
        "Download complete: %d bytes, final URL: %s",
        len(xml_bytes),
        response.url,
    )
    return xml_bytes


def _upload_to_gcs(xml_bytes: bytes, blob_name: str, raw_bucket: str, project_id: str) -> str:
    """Upload XML bytes to GCS and return the gs:// URI."""
    client = gcs.Client(project=project_id)
    bucket = client.bucket(raw_bucket)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(
        xml_bytes,
        content_type="text/xml; charset=utf-8",
        timeout=120,
    )

    gcs_uri = f"gs://{raw_bucket}/{blob_name}"
    logger.info("Uploaded %d bytes to %s", len(xml_bytes), gcs_uri)
    return gcs_uri


def _launch_dataflow_job(
    job_name: str, gcs_path: str, bq_table: str,
    project_id: str, region: str, template_path: str,
    staging_location: str, temp_location: str, dataflow_sa: str,
    network: str = "", subnetwork: str = "",
) -> str:
    """
    Launch a Dataflow Flex Template job via the REST API.

    Returns the Dataflow job ID.
    """
    # Sanitize job name (Dataflow requires lowercase alphanumeric + hyphens)
    safe_job_name = re.sub(r"[^a-z0-9-]", "-", job_name.lower())

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    authed_session = google.auth.transport.requests.AuthorizedSession(credentials)

    # Dataflow Flex Template launch endpoint
    api_url = (
        f"https://dataflow.googleapis.com/v1b3/projects/{project_id}"
        f"/locations/{region}/flexTemplates:launch"
    )

    body = {
        "launch_parameter": {
            "jobName": safe_job_name,
            "containerSpecGcsPath": template_path,
            "parameters": {
                "gcs_path": gcs_path,
                "bq_table": bq_table,
            },
            "environment": {
                "serviceAccountEmail": dataflow_sa,
                "stagingLocation": staging_location,
                "tempLocation": temp_location,
                "machineType": "n1-standard-2",
                "maxWorkers": 2,
                "numWorkers": 1,
                **({"network": network} if network else {}),
                **({"subnetwork": subnetwork} if subnetwork else {}),
                "ipConfiguration": "WORKER_IP_PRIVATE",
            },
        }
    }

    logger.info(
        "Launching Dataflow Flex Template: job=%s, template=%s, body=%s",
        safe_job_name,
        template_path,
        json.dumps(body),
    )

    response = authed_session.post(api_url, json=body, timeout=60)
    if not response.ok:
        logger.error("Dataflow API error %s: %s", response.status_code, response.text)
    response.raise_for_status()

    result = response.json()
    job_id = result.get("job", {}).get("id", "unknown")
    logger.info("Dataflow job launched: id=%s", job_id)
    return job_id

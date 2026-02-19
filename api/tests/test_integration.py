"""
Integration tests for the deployed OFAC screening API.

Prerequisites:
    export CLOUD_RUN_URL=https://ofac-screening-api-xxxx-as.a.run.app

Authentication:
    Cloud Run requires a valid Google identity token.
    The test obtains one automatically via:
      gcloud auth print-identity-token

    Or set IDENTITY_TOKEN explicitly:
      export IDENTITY_TOKEN=$(gcloud auth print-identity-token)

Run:
    python -m pytest api/tests/test_integration.py -v
"""
import os
import subprocess

import pytest
import requests

BASE_URL = os.environ.get("CLOUD_RUN_URL", "").rstrip("/")

if not BASE_URL:
    pytest.skip(
        "CLOUD_RUN_URL not set — skipping integration tests",
        allow_module_level=True,
    )


def _get_token() -> str:
    token = os.environ.get("IDENTITY_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


_TOKEN = _get_token()
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"} if _TOKEN else {}


def get(path: str, **params) -> requests.Response:
    return requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers=_HEADERS,
        timeout=30,
    )


def post(path: str, payload: dict) -> requests.Response:
    return requests.post(
        f"{BASE_URL}{path}",
        json=payload,
        headers=_HEADERS,
        timeout=60,
    )


class TestHealthIntegration:
    def test_health(self):
        resp = get("/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert "sdn_list" in data["table"]


class TestScreenIntegration:
    def test_exact_name_hit(self):
        resp = get("/screen", name="USAMA BIN LADIN")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_hits"] >= 1
        programs = {p for r in data["results"] for p in r["programs"]}
        assert "SDGT" in programs

    def test_fuzzy_binladin_typo(self):
        # One character off "USAMA BIN LADN" (drop 'i') — edit distance 1
        # Confirms fuzzy edit-distance matching is working
        resp = get("/screen", name="USAMA BIN LADN", threshold=2)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_hits"] >= 1
        all_text = " ".join(
            " ".join([
                r.get("primary_name", "") or "",
                r.get("matched_name", "") or "",
            ])
            for r in data["results"]
        ).upper()
        # OFAC stores as "LADIN"; some org names use "LADEN"
        assert "LADIN" in all_text or "LADEN" in all_text

    def test_missing_name_422(self):
        resp = get("/screen")
        assert resp.status_code == 422


class TestDocumentScreenIntegration:
    def test_sdn_name_in_document(self):
        resp = post("/screen/document", {"text": "Wire transfer authorized by USAMA BIN LADIN."})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["document_clear"] is False
        assert data["total_matches"] >= 1

    def test_fictional_names_response_structure(self):
        resp = post("/screen/document", {"text": "Payment from Zorblax Vonderhoff to Quibble McFarnsworth."})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "entities_extracted" in data
        assert "screening_results" in data
        assert "document_clear" in data
        assert "total_entities_extracted" in data
        assert "total_matches" in data


class TestEntryIntegration:
    def test_entry_7771(self):
        resp = get("/entry/7771")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["sdn_entry_id"] == 7771

    def test_entry_zero_not_found(self):
        resp = get("/entry/0")
        assert resp.status_code == 404

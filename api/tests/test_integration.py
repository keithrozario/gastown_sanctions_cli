"""
Integration tests for the deployed OFAC screening API.

Prerequisites:
    export CLOUD_RUN_URL=https://ofac-screening-api-xxxx-as.a.run.app

Run:
    python -m pytest api/tests/test_integration.py -v
"""
import os

import pytest
import requests

BASE_URL = os.environ.get("CLOUD_RUN_URL", "").rstrip("/")

if not BASE_URL:
    pytest.skip(
        "CLOUD_RUN_URL not set — skipping integration tests",
        allow_module_level=True,
    )


def get(path: str, **params) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", params=params, timeout=30)


class TestHealthIntegration:
    def test_health(self):
        resp = get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sdn_list" in data["table"]


class TestScreenIntegration:
    def test_exact_name_hit(self):
        resp = get("/screen", name="USAMA BIN LADIN")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_hits"] >= 1
        programs = {p for r in data["results"] for p in r["programs"]}
        assert "SDGT" in programs

    def test_fuzzy_saddam(self):
        resp = get("/screen", name="Sadam Husain", threshold=4)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_hits"] >= 1
        names = " ".join(
            r.get("primary_name", "") or "" for r in data["results"]
        ).upper()
        assert "SADDAM" in names or "HUSSEIN" in names

    def test_missing_name_422(self):
        resp = get("/screen")
        assert resp.status_code == 422


class TestEntryIntegration:
    def test_entry_7771(self):
        resp = get("/entry/7771")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sdn_entry_id"] == 7771

    def test_entry_zero_not_found(self):
        resp = get("/entry/0")
        assert resp.status_code == 404

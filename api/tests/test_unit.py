"""
Unit tests for the OFAC screening API.
All BigQuery calls are mocked — no GCP access required.
"""
import sys
import os

# Ensure the api package root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_row(**kwargs) -> dict:
    """Return a dict that looks like a BQ row dict after _coerce_row()."""
    defaults = {
        "sdn_entry_id": 7771,
        "sdn_type": "Individual",
        "primary_name": "BIN LADIN Usama bin Muhammad bin Awad",
        "all_name": "BIN LADIN Usama bin Muhammad bin Awad",
        "match_score": 1,
        "edit_distance": 0,
        "programs": ["SDGT"],
        "legal_authorities": [],
        "dates_of_birth": ["1957-07-30"],
        "nationalities": ["Saudi Arabia"],
        "remarks": None,
    }
    defaults.update(kwargs)
    return defaults


def _make_full_entry_row(**kwargs) -> dict:
    """Minimal full-row dict as returned by get_entry()."""
    defaults = {
        "sdn_entry_id": 7771,
        "sdn_type": "Individual",
        "primary_name": {"full_name": "BIN LADIN Usama bin Muhammad bin Awad"},
        "aliases": [],
        "programs": ["SDGT"],
        "legal_authorities": [],
        "dates_of_birth": ["1957-07-30"],
        "nationalities": ["Saudi Arabia"],
        "addresses": [],
        "id_documents": [],
        "remarks": None,
        "vessel_info": None,
        "aircraft_info": None,
        "publication_date": "2001-10-12",
        "ingestion_timestamp": "2024-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """TestClient with a mocked BQ client injected via lifespan."""
    mock_bq = MagicMock()

    with patch("main._bq_client", mock_bq):
        from main import app
        with TestClient(app) as c:
            c._mock_bq = mock_bq
            yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sdn_list" in data["table"]


class TestScreen:
    def test_screen_returns_results(self, client):
        mock_row = _make_mock_row()
        with patch("main.screen_names", return_value=[mock_row]):
            resp = client.get("/screen", params={"name": "Bin Laden"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "Bin Laden"
        assert data["total_hits"] == 1
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["sdn_entry_id"] == 7771
        assert "SDGT" in result["programs"]

    def test_screen_zero_threshold(self, client):
        with patch("main.screen_names", return_value=[]):
            resp = client.get("/screen", params={"name": "x", "threshold": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_hits"] == 0

    def test_screen_missing_name_returns_422(self, client):
        resp = client.get("/screen")
        assert resp.status_code == 422

    def test_screen_limit_applied(self, client):
        rows = [_make_mock_row(sdn_entry_id=i, all_name=f"Name {i}") for i in range(5)]
        with patch("main.screen_names", return_value=rows):
            resp = client.get("/screen", params={"name": "Name", "limit": 5})
        assert resp.status_code == 200
        assert resp.json()["total_hits"] == 5


class TestDocumentScreen:
    def test_happy_path_no_hits(self, client):
        with (
            patch("main.extract_entities", return_value=[{"name": "Alice", "entity_type": "person"}]),
            patch("main.screen_names", return_value=[]),
        ):
            resp = client.post("/screen/document", json={"text": "Payment from Alice."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_clear"] is True
        assert data["total_matches"] == 0
        assert data["total_entities_extracted"] == 1
        assert data["screening_results"][0]["is_match"] is False

    def test_sdn_hit(self, client):
        mock_row = _make_mock_row()
        with (
            patch("main.extract_entities", return_value=[{"name": "USAMA BIN LADIN", "entity_type": "person"}]),
            patch("main.screen_names", return_value=[mock_row]),
        ):
            resp = client.post("/screen/document", json={"text": "Wire from USAMA BIN LADIN received."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_clear"] is False
        assert data["total_matches"] == 1
        assert data["screening_results"][0]["is_match"] is True

    def test_multiple_entities_one_match(self, client):
        mock_row = _make_mock_row()
        entities = [
            {"name": "USAMA BIN LADIN", "entity_type": "person"},
            {"name": "Acme Corp", "entity_type": "organization"},
        ]

        def _fake_screen(client, name, threshold, limit):
            return [mock_row] if name == "USAMA BIN LADIN" else []

        with (
            patch("main.extract_entities", return_value=entities),
            patch("main.screen_names", side_effect=_fake_screen),
        ):
            resp = client.post("/screen/document", json={"text": "Transaction involving USAMA BIN LADIN and Acme Corp."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entities_extracted"] == 2
        assert data["total_matches"] == 1
        assert len(data["screening_results"]) == 2

    def test_no_entities_extracted(self, client):
        with (
            patch("main.extract_entities", return_value=[]),
            patch("main.screen_names", return_value=[]),
        ):
            resp = client.post("/screen/document", json={"text": "No named entities here."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entities_extracted"] == 0
        assert data["document_clear"] is True

    def test_empty_text_returns_422(self, client):
        resp = client.post("/screen/document", json={"text": ""})
        assert resp.status_code == 422


class TestEntry:
    def test_entry_found(self, client):
        full_row = _make_full_entry_row()
        with patch("main.get_entry", return_value=full_row):
            resp = client.get("/entry/7771")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sdn_entry_id"] == 7771

    def test_entry_not_found(self, client):
        with patch("main.get_entry", return_value=None):
            resp = client.get("/entry/9999999")
        assert resp.status_code == 404

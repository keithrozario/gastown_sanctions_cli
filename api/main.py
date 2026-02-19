import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from google.cloud import bigquery

from models import HealthResponse, ScreenResponse, ScreenResult
from queries import BQ_TABLE, get_entry, screen_names

_bq_client: bigquery.Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bq_client
    project = os.environ.get("BQ_PROJECT", BQ_TABLE.split(".")[0])
    _bq_client = bigquery.Client(project=project)
    yield
    _bq_client.close()


app = FastAPI(title="OFAC Sanctions Screening API", lifespan=lifespan)


def _client() -> bigquery.Client:
    if _bq_client is None:
        raise RuntimeError("BQ client not initialised")
    return _bq_client


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", table=BQ_TABLE)


@app.get("/screen", response_model=ScreenResponse)
def screen(
    name: Annotated[str, Query(min_length=1)],
    threshold: Annotated[int, Query(ge=0, le=10)] = 4,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    rows = screen_names(_client(), name=name, threshold=threshold, limit=limit)
    results = [
        ScreenResult(
            sdn_entry_id=r["sdn_entry_id"],
            sdn_type=r.get("sdn_type"),
            primary_name=r.get("primary_name"),
            matched_name=r.get("all_name"),
            match_score=r["match_score"],
            edit_distance=r.get("edit_distance"),
            programs=r.get("programs") or [],
            legal_authorities=r.get("legal_authorities") or [],
            dates_of_birth=r.get("dates_of_birth") or [],
            nationalities=r.get("nationalities") or [],
        )
        for r in rows
    ]
    return ScreenResponse(
        query=name,
        threshold=threshold,
        total_hits=len(results),
        results=results,
    )


@app.get("/entry/{sdn_entry_id}")
def entry(sdn_entry_id: int):
    row = get_entry(_client(), sdn_entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    # Return the raw dict — BigQuery row contains all columns
    return row

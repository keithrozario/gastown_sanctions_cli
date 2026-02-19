from typing import Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    table: str


class ScreenResult(BaseModel):
    sdn_entry_id: int
    sdn_type: Optional[str]
    primary_name: Optional[str]
    matched_name: Optional[str]
    match_score: int
    edit_distance: Optional[int]
    programs: list[str]
    legal_authorities: list[str]
    dates_of_birth: list[str]
    nationalities: list[str]


class ScreenResponse(BaseModel):
    query: str
    threshold: int
    total_hits: int
    results: list[ScreenResult]

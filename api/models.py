from typing import Optional
from pydantic import BaseModel, Field


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


class DocumentScreenRequest(BaseModel):
    text: str = Field(..., min_length=1)
    threshold: int = Field(default=4, ge=0, le=10)
    limit_per_entity: int = Field(default=5, ge=1, le=20)


class ExtractedEntity(BaseModel):
    name: str
    entity_type: str  # person | organization | vessel | aircraft


class EntityScreenResult(BaseModel):
    entity: str
    entity_type: str
    is_match: bool
    hits: list[ScreenResult]


class DocumentScreenResponse(BaseModel):
    entities_extracted: list[ExtractedEntity]
    screening_results: list[EntityScreenResult]
    document_clear: bool
    total_entities_extracted: int
    total_matches: int

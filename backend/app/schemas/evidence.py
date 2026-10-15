from pydantic import BaseModel


class EvidenceRead(BaseModel):
    id: str
    event_id: str | None = None
    extraction_result_id: str | None = None
    source_item_id: str
    field_name: str
    field_value: str | None = None
    evidence_text: str | None = None
    confidence: float | None = None
    source_title: str | None = None
    source_url: str | None = None
    created_at: str


class EvidenceListResponse(BaseModel):
    items: list[EvidenceRead]
    total: int
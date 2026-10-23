from typing import Literal

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
    verification_status: Literal["located", "unverified"] = "unverified"
    start_offset: int | None = None
    end_offset: int | None = None
    value_relation: Literal[
        "matches_current", "differs_from_current", "no_current_value", "draft",
        "unknown_field", "unavailable", "matches_draft", "differs_from_draft", "no_draft_value"
    ] = "draft"
    source_title: str | None = None
    source_url: str | None = None
    created_at: str


class EvidenceListResponse(BaseModel):
    items: list[EvidenceRead]
    total: int

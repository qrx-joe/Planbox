from pydantic import BaseModel, Field

from backend.app.schemas.extraction import ExtractedEventDraft


class EventConfirm(BaseModel):
    extracted: ExtractedEventDraft | None = None
    status: str = "interested"


class EventRead(BaseModel):
    id: str
    title: str
    event_type: str | None = None
    organizer: str | None = None
    registration_url: str | None = None
    registration_start_at: str | None = None
    registration_deadline_at: str | None = None
    event_start_at: str | None = None
    event_end_at: str | None = None
    location: str | None = None
    is_online: bool | None = None
    schedule: list[dict[str, str | None]] = Field(default_factory=list)
    fee: str | None = None
    prize: str | None = None
    team_required: str | None = None
    audience: str | None = None
    status: str
    missing_fields: list[str]
    confidence: float | None = None
    created_at: str
    updated_at: str


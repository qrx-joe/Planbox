from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.core.temporal import TimeIssue, inspect_event_times

ExtractionStatus = Literal["pending_review", "confirmed", "rejected"]


class EvidenceDraft(BaseModel):
    field: str
    value: str | None = None
    evidence: str | None = None
    confidence: float = Field(ge=0, le=1)


class ExtractedEventDraft(BaseModel):
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
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidences: list[EvidenceDraft] = Field(default_factory=list)
    time_issues: list[TimeIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def flag_unresolved_times(self) -> "ExtractedEventDraft":
        """草稿保留原始时间供纠错；重新计算问题，不能信任模型自报已经核对。"""
        previous = {(issue.field, issue.code, issue.raw_value): issue for issue in self.time_issues}
        self.time_issues = [
            previous.get((issue.field, issue.code, issue.raw_value), issue)
            for issue in inspect_event_times(self.model_dump())
        ]
        self.missing_fields = list(dict.fromkeys(
            [*self.missing_fields, *(issue.field for issue in self.time_issues)]
        ))
        return self


class ExtractionResultRead(BaseModel):
    id: str
    source_item_id: str
    status: ExtractionStatus
    extracted: ExtractedEventDraft
    missing_fields: list[str]
    confidence: float | None = None
    created_at: str
    updated_at: str


class ReviewQueueItem(BaseModel):
    extraction: ExtractionResultRead
    source_title: str | None = None
    source_url: str | None = None
    source_text: str | None = None
    source_type: str

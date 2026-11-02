import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.core.temporal import TimeIssue, inspect_event_times, validate_event_times
from backend.app.schemas.extraction import ExtractedEventDraft

# PRD 8.6 定义的活动状态全集
EventStatus = Literal[
    "inbox",
    "extracted",
    "needs_review",
    "interested",
    "applied",
    "rejected",
    "expired",
    "ongoing",
    "done",
]

# 用户可手动设置的状态（inbox/extracted 由系统流程写入，不开放）
USER_SETTABLE_STATUSES: tuple[str, ...] = (
    "needs_review",
    "interested",
    "applied",
    "rejected",
    "ongoing",
    "done",
)

# 状态机：允许的迁移路径
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "needs_review": {"interested", "applied", "rejected"},
    "interested": {"applied", "rejected", "ongoing", "expired", "done"},
    "applied": {"rejected", "ongoing", "expired", "done"},
    "rejected": {"interested", "applied"},
    "expired": {"interested", "done"},
    "ongoing": {"done", "rejected"},
    "done": {"interested"},
}

# 这些状态参与日历与提醒（PRD 8.8）
ACTIONABLE_STATUSES: frozenset[str] = frozenset({"interested", "applied"})


class EventStatusError(ValueError):
    """状态流转不合法。与迁移矩阵同处定义，供状态端点和字段修正端点共用。"""


class EventConfirm(BaseModel):
    extracted: ExtractedEventDraft | None = None
    status: str = "interested"
    expected_revision: int | None = Field(default=None, ge=0)


class EventStatusUpdate(BaseModel):
    status: EventStatus


class EventUpdate(BaseModel):
    """活动字段手工修正。未提供的字段不参与修改（exclude_unset）。"""

    title: str | None = None
    event_type: str | None = None
    organizer: str | None = None
    registration_url: str | None = None
    registration_start_at: str | None = None
    registration_deadline_at: str | None = None
    event_start_at: str | None = None
    event_end_at: str | None = None
    location: str | None = None
    is_online: bool | None = None
    schedule: list[dict[str, str | None]] | None = None
    fee: str | None = None
    prize: str | None = None
    team_required: str | None = None
    audience: str | None = None
    status: EventStatus | None = None
    notes: str | None = None
    correction_reason: str | None = Field(default=None, max_length=1000)
    source_item_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    expected_values: dict[str, object] | None = None
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_submitted_times(self) -> "EventUpdate":
        """提交字段先校验，另一半已存区间由用例层合并校验。"""
        validate_event_times(self.model_dump(exclude_unset=True))
        return self

    def to_column_values(self) -> dict[str, object]:
        """转换为可直接写入 events 表的列值，只包含显式提供的字段。"""
        data = self.model_dump(exclude_unset=True, exclude={
            "correction_reason", "source_item_id", "expected_values", "expected_revision",
        })
        values: dict[str, object] = {}
        for key, value in data.items():
            if key == "schedule":
                values["schedule_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "is_online":
                values["is_online"] = int(value) if value is not None else None
            else:
                values[key] = value
        return values


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
    notes: str | None = None
    created_at: str
    updated_at: str
    time_issues: list[TimeIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def flag_legacy_times(self) -> "EventRead":
        """旧记录仍可读取；附加问题清单，避免一次非法日期拖垮整个列表。"""
        self.time_issues = inspect_event_times(self.model_dump())
        return self


class EventListResponse(BaseModel):
    items: list[EventRead]
    total: int
    limit: int | None = None
    offset: int = 0


class DuplicateCandidate(BaseModel):
    event: EventRead
    match_reasons: list[str]
    match_score: float


class DuplicateCheckResponse(BaseModel):
    candidates: list[DuplicateCandidate]
    has_duplicates: bool


class EventMergeRequest(BaseModel):
    """把 source_id（重复项）合并进 target_id（保留项）。"""

    target_id: str
    source_id: str


class EventMergeResponse(BaseModel):
    merged: EventRead
    transferred_sources: int
    filled_fields: list[str]
    deleted_event_id: str

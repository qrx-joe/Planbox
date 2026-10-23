"""人工更正的审计信息；actor 是本地调用标识，不是认证身份。"""

from typing import Literal

from pydantic import BaseModel, Field


class CorrectionRecord(BaseModel):
    id: str
    operation_id: str
    field_name: str
    old_value: object
    new_value: object
    actor: str
    reason: str | None
    source_item_id: str | None
    action: Literal["correction", "retraction"]
    reverts_id: str | None
    created_at: str
    can_retract: bool = False
    operation_field_count: int = 1


class CorrectionRead(CorrectionRecord):
    event_id: str
    original_event_id: str


class DraftCorrectionRead(CorrectionRecord):
    extraction_result_id: str


class DraftCorrectionList(BaseModel):
    items: list[DraftCorrectionRead]
    total: int
    revision: int


class CorrectionList(BaseModel):
    items: list[CorrectionRead]
    total: int
    revision: int


class CorrectionRetraction(BaseModel):
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")

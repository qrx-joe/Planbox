from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

SourceType = Literal["wechat", "xhs", "web", "screenshot", "manual", "text"]
ParseStatus = Literal["unparsed", "pending", "parsed", "failed"]


class SourceItemCreate(BaseModel):
    source_type: SourceType = "text"
    source_url: HttpUrl | None = None
    raw_text: str | None = Field(default=None, max_length=200_000)
    title: str | None = Field(default=None, max_length=300)
    published_at: str | None = None

    @model_validator(mode="after")
    def require_content(self) -> "SourceItemCreate":
        if not self.source_url and not self.raw_text:
            raise ValueError("Either source_url or raw_text is required")
        return self


class SourceItemRead(BaseModel):
    id: str
    source_type: SourceType
    source_url: str | None = None
    raw_text: str | None = None
    title: str | None = None
    published_at: str | None = None
    captured_at: str
    parse_status: ParseStatus
    parse_error: str | None = None
    created_at: str
    updated_at: str


class SourceItemUploadResponse(BaseModel):
    id: str
    source_item_id: str
    asset_type: str
    file_path: str
    original_filename: str | None = None
    mime_type: str | None = None
    created_at: str


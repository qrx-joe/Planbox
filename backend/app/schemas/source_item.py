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
    # null 表示旧数据或非分享入口创建的条目，视为已就绪
    ingest_state: str | None = None
    # 分享入口的原始参数快照，便于回溯来源
    source_meta: dict[str, object] | None = None
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


class WechatFetchRequest(BaseModel):
    """抓取请求。

    链接走请求体而非查询参数：URL 里出现完整外链会被 CDN 层的
    WAF 判定为开放重定向或 SSRF 特征直接拦截。
    """

    source_url: str = Field(min_length=1, max_length=2000)


class WechatFetchResponse(BaseModel):
    """抓取结果。

    微信对服务端直连有风控，抓取失败是常态而非异常，
    因此不抛错，而是用 status 告知前端下一步该做什么。
    """

    source_item: SourceItemRead
    status: Literal["ready", "needs_content"]
    fetch_succeeded: bool
    fetch_error: str | None = None
    next_action: str
    extraction_started: bool = False


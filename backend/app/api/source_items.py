from fastapi import APIRouter, HTTPException, UploadFile, status

from backend.app.core.time import now_iso
from backend.app.schemas.extraction import ExtractionResultRead
from backend.app.schemas.source_item import (
    SourceItemCreate,
    SourceItemRead,
    SourceItemUploadResponse,
    WechatFetchResponse,
)
from backend.app.services.extraction_service import ExtractionService
from backend.app.services.source_service import SourceService
from backend.app.services.wechat_fetcher import (
    WechatFetcherError,
    fetch_wechat_article,
    is_wechat_url,
)

router = APIRouter()
service = SourceService()
extraction_service = ExtractionService()


@router.post("", response_model=SourceItemRead, status_code=status.HTTP_201_CREATED)
def create_source_item(payload: SourceItemCreate) -> SourceItemRead:
    return service.create(payload)


@router.post("/fetch", response_model=WechatFetchResponse)
async def fetch_and_create_wechat_article(source_url: str) -> WechatFetchResponse:
    """微信公众号文章抓取 + 自动入库。

    **注意**：微信对非客户端请求有风控，服务器直连经常抓不到。
    抓取失败时不报错，而是返回 status=needs_content 的条目，
    引导用户回文章页复制正文后补全——这是预期行为。
    """
    if not is_wechat_url(source_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不是有效的微信公众号文章链接",
        )

    fetched: dict | None = None
    fetch_error: str | None = None
    try:
        fetched = await fetch_wechat_article(source_url)
    except WechatFetcherError as exc:
        fetch_error = str(exc)

    # 抓不到正文也要建条目，否则用户复制的内容无处安放
    has_content = bool(fetched and len(fetched.get("content") or "") >= 50)

    item = service.create_with_meta(
        source_type="wechat",
        source_url=source_url,
        raw_text=fetched["content"] if has_content else None,
        title=fetched.get("title") if fetched else None,
        published_at=fetched.get("published_at") if fetched else None,
        ingest_state="ready" if has_content else "needs_content",
        source_meta=(
            {
                "entry": "wechat_fetch",
                "captured_at": now_iso(),
                "fetch_error": fetch_error,
                "fetch_succeeded": has_content,
            }
            if fetch_error or not has_content
            else None
        ),
    )

    extraction_started = False
    if has_content:
        try:
            await extraction_service.extract(item.id)
            service.mark_extract_pending(item.id)
            extraction_started = True
        except Exception:
            # 抽取失败用户仍可手动重试，不该看到创建失败
            extraction_started = False

    return WechatFetchResponse(
        source_item=item,
        status="ready" if has_content else "needs_content",
        fetch_succeeded=has_content,
        fetch_error=fetch_error,
        next_action=(
            "正文已抓取，已生成待确认草稿。"
            if has_content
            else (
                fetch_error
                or "无法自动抓取该文章正文。请在微信文章页全选正文，复制后通过分享入口补全。"
            )
        ),
        extraction_started=extraction_started,
    )


@router.get("", response_model=list[SourceItemRead])
def list_source_items() -> list[SourceItemRead]:
    return service.list()


@router.get("/{source_item_id}", response_model=SourceItemRead)
def get_source_item(source_item_id: str) -> SourceItemRead:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")
    return item


@router.post("/{source_item_id}/extract", response_model=ExtractionResultRead)
async def request_extraction(source_item_id: str) -> ExtractionResultRead:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")
    service.mark_extract_pending(source_item_id)
    return await extraction_service.extract(source_item_id)


@router.post("/{source_item_id}/assets", response_model=SourceItemUploadResponse)
async def upload_source_asset(source_item_id: str, file: UploadFile) -> SourceItemUploadResponse:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")

    asset = await service.save_asset(source_item_id, file)
    return asset

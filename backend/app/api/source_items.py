from fastapi import APIRouter, HTTPException, UploadFile, status

from backend.app.schemas.extraction import ExtractionResultRead
from backend.app.schemas.source_item import (
    SourceItemCreate,
    SourceItemRead,
    SourceItemUploadResponse,
)
from backend.app.services.extraction_service import ExtractionService
from backend.app.services.source_service import SourceService
from backend.app.services.wechat_fetcher import is_wechat_url, fetch_wechat_article, WechatFetcherError

router = APIRouter()
service = SourceService()
extraction_service = ExtractionService()


@router.post("", response_model=SourceItemRead, status_code=status.HTTP_201_CREATED)
def create_source_item(payload: SourceItemCreate) -> SourceItemRead:
    return service.create(payload)


@router.post("/fetch", response_model=SourceItemRead, status_code=status.HTTP_201_CREATED)
async def fetch_and_create_wechat_article(source_url: str) -> SourceItemRead:
    """
    微信公众号文章抓取 + 自动入库

    传入微信文章链接，自动抓取正文并创建 source_item。
    抓取成功后会直接触发 AI 提取。
    """
    if not is_wechat_url(source_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不是有效的微信公众号文章链接",
        )
    try:
        article = await fetch_wechat_article(source_url)
    except WechatFetcherError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    payload = SourceItemCreate(
        source_type="wechat",
        source_url=source_url,
        raw_text=article["content"],
        title=article["title"],
        published_at=article["published_at"],
    )
    item = service.create(payload)

    # 自动触发 AI 提取（后台处理）
    # TODO: 这里可以改成异步任务队列，避免阻塞
    try:
        await extraction_service.extract(item.id)
        service.mark_extract_pending(item.id)
    except Exception:
        pass  # 提取失败不影响创建成功

    return item


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

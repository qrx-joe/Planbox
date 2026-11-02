from fastapi import APIRouter, HTTPException, UploadFile, status

from backend.app.core.time import now_iso
from backend.app.repositories.source_item_repository import SourceStateConflict
from backend.app.schemas.extraction import ExtractionResultRead
from backend.app.schemas.source_item import (
    SourceFetchResponse,
    SourceItemCreate,
    SourceItemRead,
    SourceItemUploadResponse,
    WechatFetchRequest,
)
from backend.app.services.extraction_service import ExtractionFailed, ExtractionService
from backend.app.services.source_service import SourceService
from backend.app.services.wechat_fetcher import (
    WechatFetcherError,
    fetch_wechat_article,
    is_wechat_url,
)
from backend.app.services.xiaohongshu_fetcher import (
    MIN_CONTENT_LENGTH,
    XiaohongshuFetcherError,
    is_xiaohongshu_note_url,
    is_xiaohongshu_short_link,
)

router = APIRouter()
service = SourceService()
extraction_service = ExtractionService()


@router.post("", response_model=SourceItemRead, status_code=status.HTTP_201_CREATED)
def create_source_item(payload: SourceItemCreate) -> SourceItemRead:
    return service.create(payload)


@router.post("/fetch", response_model=SourceFetchResponse)
async def fetch_and_create_source_article(
    payload: WechatFetchRequest,
) -> SourceFetchResponse:
    """公众号文章与小红书笔记抓取 + 自动入库。

    **注意**：两个平台对服务端直连都不友好。微信有风控，小红书笔记正文
    不在服务端 HTML 里（真实浏览器实测会返回「IP存在风险」）。
    抓取失败时不报错，而是返回 status=needs_content 的条目，
    引导用户粘贴正文或上传截图——这是预期行为，不是异常。

    链接从请求体读取而非查询参数：URL 里出现完整外链
    会被 CDN 层的 WAF 当作开放重定向/SSRF 特征直接拦截（403）。
    """
    source_url = payload.source_url

    if is_wechat_url(source_url):
        return await _fetch_wechat(source_url)
    if is_xiaohongshu_note_url(source_url) or is_xiaohongshu_short_link(source_url):
        return await _fetch_xiaohongshu(source_url)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="不是有效的微信公众号文章或小红书笔记链接",
    )


async def _fetch_wechat(source_url: str) -> SourceFetchResponse:
    """抓取微信公众号文章。"""
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
            extraction_started = True
        except Exception:
            # 抽取失败用户仍可手动重试，不该看到创建失败
            extraction_started = False

    item = get_source_item(item.id)

    return SourceFetchResponse(
        source_item=item,
        status="ready" if has_content else "needs_content",
        fetch_succeeded=has_content,
        fetch_error=fetch_error,
        next_action=(
            "正文已抓取，已生成待确认草稿。"
            if extraction_started
            else "正文已保存，抽取失败，请在收集箱重试抽取。" if has_content
            else (
                fetch_error
                or "无法自动抓取该文章正文。请在微信文章页全选正文，复制后通过分享入口补全。"
            )
        ),
        extraction_started=extraction_started,
    )


async def _fetch_xiaohongshu(source_url: str) -> SourceFetchResponse:
    """抓取小红书笔记。

    小红书笔记正文不在服务端 HTML 里——页面只有骨架，真实内容由前端 JS
    请求带设备签名的接口取得。所以这里多数情况下取不到正文，属预期结果：
    返回 needs_content，让用户粘贴正文或上传截图。

    仍然值得一试的原因：短链能被展开成带 xsec_token 的完整地址，
    某些情况下服务端会直接把笔记数据渲染进初始状态。
    """
    from backend.app.services.xiaohongshu_fetcher import XiaohongshuNoteFetcher

    fetched = None
    fetch_error: str | None = None
    resolved_url: str | None = None
    try:
        fetched = await _fetch_xhs_async(XiaohongshuNoteFetcher(), source_url)
        resolved_url = fetched.get("source_url")
    except XiaohongshuFetcherError as exc:
        fetch_error = str(exc)

    has_content = bool(fetched and len(fetched.get("content") or "") >= MIN_CONTENT_LENGTH)

    item = service.create_with_meta(
        source_type="xhs",
        source_url=resolved_url or source_url,
        raw_text=fetched["content"] if has_content else None,
        title=fetched.get("title") if fetched else None,
        published_at=fetched.get("published_at") if fetched else None,
        ingest_state="ready" if has_content else "needs_content",
        source_meta=(
            {
                "entry": "xiaohongshu_fetch",
                "captured_at": now_iso(),
                "fetch_error": fetch_error,
                "fetch_succeeded": has_content,
                "origin_url": source_url,
                "resolved_url": resolved_url,
            }
            if fetch_error or not has_content
            else None
        ),
    )

    extraction_started = False
    if has_content:
        try:
            await extraction_service.extract(item.id)
            extraction_started = True
        except Exception:
            # 抽取失败用户仍可手动重试，不该看到创建失败
            extraction_started = False

    item = get_source_item(item.id)

    return SourceFetchResponse(
        source_item=item,
        status="ready" if has_content else "needs_content",
        fetch_succeeded=has_content,
        fetch_error=fetch_error,
        next_action=(
            "正文已抓取，已生成待确认草稿。"
            if extraction_started
            else "正文已保存，抽取失败，请在收集箱重试抽取。" if has_content
            else (
                fetch_error
                or "小红书笔记正文无法直接抓取（平台不在服务端返回正文）。"
                "请在笔记页复制正文粘贴，或上传笔记截图。"
            )
        ),
        extraction_started=extraction_started,
        resolved_url=resolved_url,
    )


async def _fetch_xhs_async(fetcher, source_url: str) -> dict:
    """在线程里跑同步抓取，避免阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(fetcher.fetch, source_url)


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
    """抽取或复用已保存结果；状态门禁、认领和持久化由服务统一执行。"""
    try:
        return await extraction_service.extract(source_item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source item not found") from exc
    except SourceStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/{source_item_id}/assets",
    response_model=SourceItemUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source_asset(
    source_item_id: str, file: UploadFile
) -> SourceItemUploadResponse:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")

    try:
        return await service.save_asset(source_item_id, file)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc

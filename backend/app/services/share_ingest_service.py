"""小程序分享入口处理。

两段式：
  1. share_intake       接收分享唤起参数，建立条目。抓不到正文时标记 needs_content
  2. complete_content   用户粘贴正文后补全，触发 AI 抽取

关键约束：微信不会告知小程序用户正在读哪篇文章。
分享能拿到的只有小程序自定义的 path query 和转发标题，
正文必须由用户粘贴。这是生态限制，不是实现取舍。
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.core.time import now_iso
from backend.app.repositories.source_item_repository import SourceItemRepository
from backend.app.schemas.share import (
    ContentCompleteRequest,
    ContentCompleteResponse,
    ShareIntakeRequest,
    ShareIntakeResponse,
)
from backend.app.services.source_service import SourceService
from backend.app.services.wechat_fetcher import (
    WechatFetcherError,
    is_wechat_url,
)

# 剪贴板粘贴正文时常见的噪声前缀，去掉后能让 LLM 抽取更准
NOISE_PREFIXES = (
    "复制打开微信",
    "长按识别二维码",
    "继续滑动看下一个",
    "轻触阅读原文",
    "点击上方蓝字",
)
NOISE_SUFFIXES = ("阅读原文", "点击蓝字关注我们", "预览时标签不可点")


class ShareIngestService:
    def __init__(
        self,
        source_service: SourceService | None = None,
        repository: SourceItemRepository | None = None,
    ) -> None:
        self.source_service = source_service or SourceService()
        self.repository = repository or SourceItemRepository()

    async def share_intake(self, payload: ShareIntakeRequest) -> ShareIntakeResponse:
        """处理分享唤起。能抓到正文就直接抽取，抓不到就等用户粘贴。"""
        article_url = _clean_url(payload.article_url)
        is_wechat = bool(article_url and is_wechat_url(article_url))

        # 分享标题常常就是文章标题，但没有正文时它只是提示，不能当原文存证
        title = _clean_text(payload.title)
        raw_text = _clean_text(payload.summary)

        fetch_attempted = False
        fetch_succeeded = False
        fetch_error: str | None = None

        if is_wechat and article_url:
            fetched = await self._try_fetch(article_url)
            fetch_attempted = True
            if fetched is not None:
                fetch_succeeded = True
                raw_text = fetched["content"]
                title = fetched.get("title") or title
            else:
                fetch_error = (
                    "服务端无法直接抓取该文章正文，微信对非客户端请求有风控。"
                    "请在文章页全选正文，复制后粘贴到下方。"
                )

        # 有正文才认为就绪
        has_content = bool(raw_text and len(raw_text) >= MIN_CONTENT_LENGTH)
        state = "ready" if has_content else "needs_content"

        source_type = "wechat" if is_wechat else ("web" if article_url else "text")

        item = self.source_service.create_with_meta(
            source_type=source_type,
            source_url=article_url,
            raw_text=raw_text if has_content else None,
            title=title,
            published_at=None,
            ingest_state=state,
            source_meta=_build_meta(payload, article_url, fetch_error),
        )

        extraction_started = False
        if has_content:
            extraction_started = await self._extract(item.id)

        return ShareIntakeResponse(
            source_item_id=item.id,
            state=state,
            title=item.title,
            source_url=item.source_url,
            is_wechat_article=is_wechat,
            fetch_attempted=fetch_attempted,
            fetch_succeeded=fetch_succeeded,
            fetch_error=fetch_error,
            next_action=self._next_action(state, fetch_error),
            extraction_started=extraction_started,
        )

    async def complete_content(
        self, source_item_id: str, payload: ContentCompleteRequest
    ) -> ContentCompleteResponse:
        """用户粘贴正文后补全条目。已就绪的条目视为重复提交，直接返回原状态。"""
        item = self.source_service.get(source_item_id)
        if item is None:
            raise KeyError(source_item_id)

        raw_text = _clean_text(payload.raw_text) or ""
        if len(raw_text) < MIN_CONTENT_LENGTH:
            raise ValueError(
                f"正文太短（{len(raw_text)} 字），至少需要 {MIN_CONTENT_LENGTH} 字才能提取活动信息"
            )

        # 幂等：已经补全过且正文一致时不重复抽取
        already_ready = item.ingest_state == "ready" and item.raw_text == raw_text

        title = _clean_text(payload.title) or item.title
        updated = self.source_service.update_content(
            source_item_id=source_item_id,
            raw_text=raw_text,
            title=title,
            published_at=payload.published_at or item.published_at,
        )

        extraction_started = False
        extraction_id: str | None = None
        if payload.auto_extract and not already_ready:
            extraction_started = await self._extract(source_item_id)
            if extraction_started:
                extraction_id = _latest_extraction_id(source_item_id)

        return ContentCompleteResponse(
            source_item_id=updated.id,
            state="ready",
            title=updated.title,
            extraction_started=extraction_started,
            extraction_id=extraction_id,
        )

    # ---------- 内部 ----------

    async def _try_fetch(self, url: str) -> dict[str, Any] | None:
        """尽力抓取，失败返回 None 而不抛异常——降级比报错对用户更有用。"""
        from backend.app.services.wechat_fetcher import fetch_wechat_article

        try:
            return await fetch_wechat_article(url)
        except (WechatFetcherError, Exception):
            # 抓取是可选增强，任何失败都不应阻断入库
            return None

    async def _extract(self, source_item_id: str) -> bool:
        """触发 AI 抽取。失败不影响条目本身已创建的事实。"""
        from backend.app.services.extraction_service import ExtractionService

        try:
            await ExtractionService().extract(source_item_id)
            self.source_service.mark_extract_pending(source_item_id)
            return True
        except Exception:
            # 抽取失败用户仍可手动重试，不该看到创建失败
            return False

    def _next_action(self, state: str, fetch_error: str | None) -> str:
        if state == "ready":
            return "正文已就绪，已生成待确认草稿。"
        if fetch_error:
            return fetch_error
        return (
            "分享唤起未携带文章正文。请在微信文章页长按全选复制正文，"
            "回到本页面粘贴后提交。"
        )


# 低于此长度视为「不是正文」，通常是标题或摘要
MIN_CONTENT_LENGTH = 50


def _clean_text(value: str | None) -> str | None:
    """去掉复制粘贴带进来的微信提示语。"""
    if not value:
        return None
    text = value.strip()
    for prefix in NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    for suffix in NOISE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text or None


def _clean_url(url: str | None) -> str | None:
    if not url:
        return None
    trimmed = url.strip()
    if not trimmed.startswith(("http://", "https://")):
        return None
    return trimmed


def _build_meta(
    payload: ShareIntakeRequest, article_url: str | None, fetch_error: str | None
) -> dict[str, object]:
    """记录分享原始参数，用于回溯「这条是从哪来的」。"""
    meta: dict[str, object] = {
        "entry": "wechat_share",
        "captured_at": now_iso(),
    }
    for key in ("scene", "query", "title", "summary", "referer", "webview_url"):
        value = getattr(payload, key, None)
        if value:
            meta[key] = value
    if article_url:
        meta["article_url"] = article_url
    if fetch_error:
        meta["fetch_error"] = fetch_error
    return meta


def _latest_extraction_id(source_item_id: str) -> str | None:
    from backend.app.db.database import connect

    with connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM extraction_results
            WHERE source_item_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (source_item_id,),
        ).fetchone()
    return str(row["id"]) if row else None


# 供 source_service 复用
def dumps_meta(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False)
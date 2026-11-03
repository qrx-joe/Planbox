"""通过受限的 HTTPS 请求读取微信文章；平台拒绝访问时由调用方提供正文补全。"""

from __future__ import annotations

import re
from typing import TypedDict
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests

MAX_REDIRECTS = 5

# 传输层上限：按 Content-Length 预检，用于在读正文前快速拒绝超大响应。
MAX_WIRE_BYTES = 4 * 1024 * 1024

# 解压后上限：微信文章页 gzip 传输约 0.8 MiB，但解压后普遍 2.5~3.5 MiB，
# 体积几乎全部来自内联脚本与样式，正文本身只有几十 KB。
# 实测 11 个真实样本解压后 2.46~3.52 MiB，因此上限取 8 MiB 留出余量，
# 同时仍然给高压缩比响应（gzip 炸弹）设了硬边界。
MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024


class WechatArticle(TypedDict):
    title: str
    author: str | None
    content: str
    published_at: str | None
    source_url: str


class WechatFetcherError(Exception):
    """抓取失败的异常"""


def is_wechat_url(url: str) -> bool:
    """校验 HTTPS 微信文章短/长链接，不发请求，不解码整个 URL。

    仅接受官方 host、默认/443 端口及无用户信息的 URL；短链允许 URL-safe
    文章 ID，长链要求 __biz、mid、idx。查询值保留原编码以免改变文章参数。
    返回 False 表示格式不受支持，不代表文章是否真实存在或可访问。
    """
    if not url or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return False
    if re.search(r"%(?![0-9a-fA-F]{2})", url):
        return False
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not re.fullmatch(
            r"mp\.weixin\.qq\.com(?::443)?", parts.netloc, re.IGNORECASE
        ):
            return False
        if re.fullmatch(r"/s/[A-Za-z0-9_-]+", unquote(parts.path)):
            return True
        if parts.path == "/s":
            query = parse_qs(parts.query, keep_blank_values=True)
            return all(
                len(query.get(key, [])) == 1 and query[key][0].strip()
                for key in ("__biz", "mid", "idx")
            )
    except ValueError:
        return False
    return False


class WechatArticleFetcher:
    """使用 requests 抓取文章；最多跟随 5 跳同域文章链接。

    传输体积按 Content-Length 预检上限，解压后体积另有独立上限——
    两者口径不同：微信文章页 gzip 传输很小但解压后有数 MiB。
    """

    def __init__(self, timeout: int | None = None):
        from backend.app.core.config import settings

        self.timeout = timeout or settings.wechat_fetch_timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    def fetch(self, url: str) -> WechatArticle:
        """抓取并返回文章，source_url 保留输入链接，网络读取在当前线程执行。

        每跳请求使用 timeout 秒的连接/读取超时，不自动重试；这不是总耗时上限。
        非法链接、不可用响应或缺失正文抛 WechatFetcherError，错误不携带原始 URL。
        """
        if not is_wechat_url(url):
            raise WechatFetcherError("不是有效的微信文章链接")

        try:
            article = self._try_direct_fetch(url)
            if article and article["content"]:
                return article
        except (requests.RequestException, ValueError, LookupError) as exc:
            raise WechatFetcherError("文章请求或响应不可用，请复制并粘贴正文补全。") from exc

        raise WechatFetcherError("无法获取文章正文，请复制并粘贴正文补全。")

    def _try_direct_fetch(self, url: str) -> WechatArticle | None:
        """手动跟随已校验的文章重定向，流式限制解压后字节数及响应类型。"""
        current_url = url
        for hop in range(MAX_REDIRECTS + 1):
            # 禁止 requests 自动跟随，确保下一跳在连接前也使用同一校验。
            with self.session.get(
                current_url, timeout=self.timeout, allow_redirects=False, stream=True
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    # urljoin 会移除部分控制字符，须先校验原始 Location。
                    if not location or re.search(r"[\s\x00-\x1f\x7f\\]", location):
                        raise WechatFetcherError("文章跳转不可用，请复制并粘贴正文补全。")
                    target = urljoin(current_url, location)
                    if not is_wechat_url(target) or hop == MAX_REDIRECTS:
                        raise WechatFetcherError("文章跳转不可用，请复制并粘贴正文补全。")
                    current_url = target
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() not in (
                    "text/html", "application/xhtml+xml"
                ):
                    raise WechatFetcherError("文章响应不是 HTML，请复制并粘贴正文补全。")
                declared_size = response.headers.get("Content-Length", "")
                if declared_size.isdigit() and int(declared_size) > MAX_WIRE_BYTES:
                    raise WechatFetcherError("文章响应过大，请复制并粘贴正文补全。")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    # iter_content 给出的是解压后字节，所以这里限制的是解压后体积。
                    # 真实微信文章解压后 2.5~3.5 MiB，不能用传输量口径卡它。
                    if len(body) + len(chunk) > MAX_DECOMPRESSED_BYTES:
                        raise WechatFetcherError("文章响应过大，请复制并粘贴正文补全。")
                    body.extend(chunk)
                # 微信正文为 UTF-8；显式 charset 存在时沿用服务端声明。
                encoding = response.encoding if "charset=" in content_type.lower() else "utf-8"
                return self._parse_html(body.decode(encoding or "utf-8", errors="replace"), url)
        return None

    def _parse_html(self, html: str, source_url: str) -> WechatArticle | None:
        """解析 HTML 内容，提取文章信息"""
        import json

        # 尝试从 HTML 中提取 JSON 数据 (微信文章嵌入的元数据)
        title_match = re.search(r'"title":\s*"([^"]+)"', html)
        author_match = re.search(r'"author":\s*"([^"]+)"', html)
        date_match = re.search(r'"publish_time":\s*"([^"]+)"', html)
        content_match = re.search(r'"content":\s*(".*?")', html, re.DOTALL)

        if title_match:
            title = self._unescape_json_string(title_match.group(1))
        else:
            # 真实微信页面的 <title> 标签是空的，标题放在 og:title meta 里，
            # 所以先认 og:title，再退回 <title> 标签。
            title = self._extract_og_title(html) or ""
            if not title:
                title_match = re.search(r"<title>([^<]+)</title>", html)
                title = title_match.group(1) if title_match else "未命名文章"

        author = author_match.group(1) if author_match else None
        if author:
            author = self._unescape_json_string(author)

        published_at = date_match.group(1) if date_match else None

        content = ""
        if content_match:
            try:
                content = json.loads(content_match.group(1))
                content = self._unescape_json_string(content)
            except json.JSONDecodeError:
                pass

        # 备用方法：从 HTML 中提取正文
        if not content:
            content = self._extract_content_from_html(html)

        return WechatArticle(
            title=title,
            author=author,
            content=content,
            published_at=published_at,
            source_url=source_url,
        )

    def _extract_og_title(self, html: str) -> str | None:
        """从 og:title meta 取标题。

        真实微信文章页的 <title> 为空，标题只出现在 og:title 里；
        缺少它会让条目标题退化成「未命名文章」，用户在列表里认不出条目。
        """
        match = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if not match:
            # 属性顺序不固定：content 在前时也要能取到。
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
                html,
                re.IGNORECASE,
            )
        if not match:
            return None
        return self._html_unescape(match.group(1)).strip() or None

    def _extract_content_from_html(self, html: str) -> str:
        """从 HTML 中提取正文内容"""
        # 移除 script 和 style 标签
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # 尝试找到 id="js_content" 的元素
        content_match = re.search(
            r'id="js_content"[^>]*>(.*?)(?=<div|$)', html, re.DOTALL | re.IGNORECASE
        )
        if content_match:
            return self._clean_html(content_match.group(1))

        # 尝试找到 class="rich_media_content" 的元素
        content_match = re.search(
            r'class="rich_media_content"[^>]*>(.*?)(?=<div|$)',
            html, re.DOTALL | re.IGNORECASE,
        )
        if content_match:
            return self._clean_html(content_match.group(1))

        return ""

    def _clean_html(self, html: str) -> str:
        """清理 HTML，提取纯文本"""
        # 移除所有 HTML 标签
        text = re.sub(r"<[^>]+>", "", html)
        # 解码 HTML 实体
        text = self._html_unescape(text)
        # 清理多余空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _unescape_json_string(self, s: str) -> str:
        """处理 JSON 字符串中的转义字符"""
        import html
        s = s.replace("\\'", "'")
        s = s.replace('\\"', '"')
        s = s.replace("\\n", "\n")
        s = s.replace("\\r", "\r")
        s = s.replace("\\\\", "\\")
        return html.unescape(s)

    def _html_unescape(self, text: str) -> str:
        """HTML 解码"""
        import html
        return html.unescape(text)


# 默认实例
_default_fetcher: WechatArticleFetcher | None = None


def get_fetcher() -> WechatArticleFetcher:
    """获取默认的抓取器实例"""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = WechatArticleFetcher()
    return _default_fetcher


async def fetch_wechat_article(url: str) -> WechatArticle:
    """
    异步封装：抓取微信文章

    用法:
        article = await fetch_wechat_article("https://mp.weixin.qq.com/s/xxxxx")
    """
    import asyncio

    fetcher = get_fetcher()
    return await asyncio.to_thread(fetcher.fetch, url)

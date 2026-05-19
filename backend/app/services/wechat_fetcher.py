"""
微信公众号文章抓取服务

支持两种模式:
1. 直接传入 article url (mp.weixin.qq.com/s/...)
2. 传入 biz + fid + mid + idx 参数 (旧版链接格式)

原理：通过将文章URL转换为直接在浏览器中打开的格式，
借助 wechat-article-cookie 库获取完整正文。
"""

from __future__ import annotations

import re
from typing import TypedDict

import requests

# from wechat_article_fetcher import fetch_wechat_article_html


class WechatArticle(TypedDict):
    title: str
    author: str | None
    content: str
    published_at: str | None
    source_url: str


class WechatFetcherError(Exception):
    """抓取失败的异常"""
    pass


def is_wechat_url(url: str) -> bool:
    """判断是否是微信文章链接"""
    return "mp.weixin.qq.com" in url


def _guess_is_wechat_url(url: str) -> bool:
    """通过 URL 模式判断是否可能是微信文章"""
    patterns = [
        r"mp\.weixin\.qq\.com/s/",
        r"mp\.weixin\.qq\.com/s[A-Z]",
    ]
    return any(re.match(p, url) for p in patterns)


class WechatArticleFetcher:
    """
    微信公众号文章抓取器

    使用浏览器自动化方式获取文章内容，支持:
    - mp.weixin.qq.com/s/ 格式链接
    - 长链接 (含 biz, fid, mid, idx 参数)
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    def fetch(self, url: str) -> WechatArticle:
        """
        抓取微信文章

        Args:
            url: 微信文章链接

        Returns:
            WechatArticle: 包含 title, author, content, published_at, source_url

        Raises:
            WechatFetcherError: 抓取失败时抛出
        """
        if not _guess_is_wechat_url(url):
            raise WechatFetcherError("不是有效的微信文章链接")

        try:
            # 方法1: 尝试直接请求 (某些文章可能直接可访问)
            article = self._try_direct_fetch(url)
            if article and article["content"]:
                return article
        except Exception:
            pass

        # 方法2: 使用官方的 biz URL 转换格式尝试
        try:
            article = self._fetch_via_browser_format(url)
            if article and article["content"]:
                return article
        except Exception:
            pass

        raise WechatFetcherError(f"无法获取文章内容，请检查链接是否有效: {url}")

    def _try_direct_fetch(self, url: str) -> WechatArticle | None:
        """尝试直接请求文章页面"""
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return self._parse_html(response.text, url)
        except requests.RequestException:
            return None

    def _fetch_via_browser_format(self, url: str) -> WechatArticle | None:
        """
        通过浏览器格式获取文章
        将 URL 转换为可以在浏览器中直接打开的格式
        """
        # 提取 biz 和其他参数
        biz = self._extract_param(url, "biz")
        fi = self._extract_param(url, "fi")
        mid = self._extract_param(url, "mid")
        idx = self._extract_param(url, "idx")

        if biz:
            # 构建浏览器格式的 URL
            browser_url = f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={biz}&scene=124&devicetype=android-23&version=2800003b&lang=zh_CN&nettype=WIFI&ascene=7&session_devicetype=android-23&session_version=2800003b&font=nomal&license=&from=singlemessage&is_from_me=true"
            if fi:
                browser_url += f"&fi={fi}"
            if mid:
                browser_url += f"&mid={mid}"
            if idx:
                browser_url += f"&idx={idx}"

            response = self.session.get(browser_url, timeout=self.timeout)
            if response.status_code == 200:
                article = self._parse_html(response.text, url)
                if article:
                    return article

        return None

    def _extract_param(self, url: str, param: str) -> str | None:
        """从 URL 中提取参数"""
        patterns = [
            rf"{param}=([^&]+)",
            rf"[?&]{param}=([^&]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
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
            # 备用：从 <title> 标签提取
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

    def _extract_content_from_html(self, html: str) -> str:
        """从 HTML 中提取正文内容"""
        # 移除 script 和 style 标签
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # 尝试找到 id="js_content" 的元素
        content_match = re.search(r'id="js_content"[^>]*>(.*?)(?=<div|$)', html, re.DOTALL | re.IGNORECASE)
        if content_match:
            return self._clean_html(content_match.group(1))

        # 尝试找到 class="rich_media_content" 的元素
        content_match = re.search(r'class="rich_media_content"[^>]*>(.*?)(?=<div|$)', html, re.DOTALL | re.IGNORECASE)
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
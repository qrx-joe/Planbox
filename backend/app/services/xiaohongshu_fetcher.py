"""小红书笔记的只读抓取：解析链接并尽力提取公开正文。

小红书与微信的架构差异决定了这里的做法：

- 笔记正文**不在服务端 HTML 里**。页面 HTML 只有骨架，真实内容由前端 JS
  请求 `edith.xiaohongshu.com/api/sns/h5/v1/note_info` 取得，而该接口需要
  设备签名头（`x-s`）与有效风控状态，裸请求返回 406/461。
- 因此本模块**不承诺读到正文**。它做三件确实可行的事：
  1. 严格校验链接形态，只接受官方 host；
  2. 解析短链（`xhslink.com`）的 302 跳转，还原出带 `xsec_token` 的完整
     笔记链接——这一步不依赖风控，是真实可用的；
  3. 尽力从 HTML 内嵌状态里提取正文；取不到就明确降级，让用户粘贴或截图。

不要在这里加入签名伪造、验证码绕过或代理池。这些超出只读适配的边界，
且违反平台规则。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict
from urllib.parse import urlsplit

import requests

# 传输层上限：按 Content-Length 预检，用于在读正文前快速拒绝超大响应。
MAX_WIRE_BYTES = 4 * 1024 * 1024

# 解压后上限：iter_content 给出的是解压后字节，这里限制解压后体积。
MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024

# 笔记 ID：24 位十六进制。用于识别链接指向单篇笔记。
NOTE_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")

# 判定「已抓到正文」的最小字数。
# 微信文章普遍是长文，50 字是合适门槛；小红书笔记短得多，
# 活动通知常常只有三四十字（标题式正文），沿用 50 会把有效笔记误判成
# 没抓到，所以这里用更贴合小红书的 20 字。
MIN_CONTENT_LENGTH = 20

# 受支持的官方 host。xhslink.com 是官方短链域名，其余为笔记页域名。
NOTE_HOSTS = ("www.xiaohongshu.com", "xiaohongshu.com")
SHORT_LINK_HOSTS = ("xhslink.com",)

# 笔记页路径前缀。profile 是用户主页，collection 是合集页——
# 它们不是单篇笔记，正文语义与笔记不同，不做正文提取。
NOTE_PATH_PREFIXES = ("/explore/", "/discovery/item/")


class XiaohongshuNote(TypedDict):
    """抓取结果。正文取不到时 content 为空字符串，不返回 None。"""

    title: str | None
    content: str
    author: str | None
    published_at: str | None
    source_url: str
    note_id: str | None


class XiaohongshuFetcherError(Exception):
    """抓取失败的异常。消息不携带原始 URL。"""


def is_xiaohongshu_note_url(url: str) -> bool:
    """校验是否为受支持的官方笔记链接，不发请求。

    仅接受 https、官方 host（可带 :443）、无用户信息，且路径指向单篇笔记
    （`/explore/<24位ID>` 或 `/discovery/item/<24位ID>`）。
    用户主页与合集页不是单篇笔记，这里不接受，避免误当作笔记处理。

    返回 False 只表示格式不受支持，不代表笔记存在或可访问。
    """
    if not url or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = parts.netloc.lower()
    if ":" in host:
        host, _, port = host.rpartition(":")
        if port not in ("443", ""):
            return False
    if host not in NOTE_HOSTS:
        return False
    if parts.username or parts.password:
        return False
    path = parts.path
    for prefix in NOTE_PATH_PREFIXES:
        if path.startswith(prefix):
            note_id = path[len(prefix) :]
            return bool(NOTE_ID_PATTERN.match(note_id))
    return False


def is_xiaohongshu_short_link(url: str) -> bool:
    """校验是否为官方短链。短链需要一次跳转才能还原真实笔记链接。"""
    if not url or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = parts.netloc.lower()
    if ":" in host:
        host, _, port = host.rpartition(":")
        if port not in ("443", ""):
            return False
    if host not in SHORT_LINK_HOSTS:
        return False
    return bool(parts.path.strip("/"))


def extract_note_id(url: str) -> str | None:
    """从笔记链接中取出 24 位笔记 ID；不是笔记链接时返回 None。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    for prefix in NOTE_PATH_PREFIXES:
        if parts.path.startswith(prefix):
            note_id = parts.path[len(prefix) :]
            if NOTE_ID_PATTERN.match(note_id):
                return note_id
    return None


class XiaohongshuNoteFetcher:
    """抓取小红书笔记。

    短链走一次受限跳转还原真实链接；笔记页尽力提取内嵌状态里的正文，
    取不到时返回空 content，由调用方降级为「需要粘贴正文」。
    """

    def __init__(self, timeout: int | None = None):
        from backend.app.core.config import settings

        self.timeout = timeout or settings.xiaohongshu_fetch_timeout
        self.session = requests.Session()
        self.session.headers.update({
            # 桌面 UA。移动端 UA 返回的页面结构不同（noteData 而非 note），
            # 且桌面结构包含 noteDetailMap 这样的笔记数据键位。
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    def resolve(self, url: str) -> str:
        """把短链解析为完整官方链接；已是笔记链接时原样返回。

        短链的 302 Location 会带上 `xsec_token`，这是后续取内容的必要参数。

        只跟一跳：官方短链服务直接把 Location 指向带 token 的目标页，
        再往下跳会进入 /login 登录页，反而丢掉 token。
        目标不一定是单篇笔记——官方分享链接也可能指向用户主页或合集，
        这里接受任意官方页面，能否提取正文由后续阶段判断。
        """
        if is_xiaohongshu_note_url(url):
            return url
        if not is_xiaohongshu_short_link(url):
            raise XiaohongshuFetcherError("不是有效的小红书链接")

        try:
            with self.session.get(
                url, timeout=self.timeout, allow_redirects=False, stream=True
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    if not location or re.search(r"[\s\x00-\x1f\x7f\\]", location):
                        raise XiaohongshuFetcherError(
                            "小红书短链跳转不可用，请直接复制完整链接。"
                        )
                    target = _join_url(url, location)
                    if not _is_official_xhs_url(target):
                        raise XiaohongshuFetcherError(
                            "小红书短链跳转不可用，请直接复制完整链接。"
                        )
                    return target
                # 短链服务可能直接返回 200 页面，跳转信息在 HTML 里。
                body = self._read_body(response)
                return self._resolve_from_html(body, url)
        except requests.RequestException as exc:
            raise XiaohongshuFetcherError(
                "小红书链接请求失败，请复制完整链接或粘贴正文。"
            ) from exc

    def fetch(self, url: str) -> XiaohongshuNote:
        """抓取笔记内容。

        正文取不到时返回 content 为空的笔记，不抛异常——这是平台的正常
        限制，调用方据此引导用户粘贴正文或上传截图。
        """
        resolved = self.resolve(url)
        note_id = extract_note_id(resolved)

        try:
            with self.session.get(
                resolved, timeout=self.timeout, allow_redirects=False, stream=True
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() not in (
                    "text/html", "application/xhtml+xml"
                ):
                    raise XiaohongshuFetcherError(
                        "小红书响应不是 HTML，请粘贴正文或上传截图。"
                    )
                body = self._read_body(response)
        except requests.RequestException as exc:
            raise XiaohongshuFetcherError(
                "小红书链接请求失败，请粘贴正文或上传截图。"
            ) from exc

        return self._parse_html(body, resolved, note_id)

    # ---------- 内部 ----------

    def _read_body(self, response: requests.Response) -> str:
        """流式读取并限制解压后体积，返回解码后的文本。"""
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_WIRE_BYTES:
            raise XiaohongshuFetcherError("小红书响应过大，请粘贴正文或上传截图。")
        buffer = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if len(buffer) + len(chunk) > MAX_DECOMPRESSED_BYTES:
                raise XiaohongshuFetcherError("小红书响应过大，请粘贴正文或上传截图。")
            buffer.extend(chunk)
        content_type = response.headers.get("Content-Type", "")
        encoding = response.encoding if "charset=" in content_type.lower() else "utf-8"
        return buffer.decode(encoding or "utf-8", errors="replace")

    def _resolve_from_html(self, html: str, base_url: str) -> str:
        """从短链返回的 HTML 中找真实官方链接。

        页面里可能出现笔记页，也可能只有用户主页；只要指向官方 host 就接受，
        能否提取正文由后续阶段判断。
        """
        match = re.search(
            r'https?://(?:www\.)?xiaohongshu\.com/'
            r'(?:explore|discovery/item|user/profile|collection)/[0-9a-zA-Z]+'
            r'[^\"\'\s<>]*',
            html,
        )
        if not match:
            raise XiaohongshuFetcherError(
                "无法从短链解析出笔记地址，请直接复制完整链接。"
            )
        candidate = _join_url(base_url, match.group(0))
        if not _is_official_xhs_url(candidate):
            raise XiaohongshuFetcherError(
                "无法从短链解析出笔记地址，请直接复制完整链接。"
            )
        return candidate

    def _parse_html(
        self, html: str, source_url: str, note_id: str | None
    ) -> XiaohongshuNote:
        """解析页面内嵌状态，取出标题与正文。

        小红书把初始状态放在 `window.__INITIAL_STATE__` 里，其中含 JS 字面量
        `undefined`，直接 json.loads 会失败，需先转成 null。
        """
        state = _extract_initial_state(html)
        if state is None:
            return XiaohongshuNote(
                title=None, content="", author=None, published_at=None,
                source_url=source_url, note_id=note_id,
            )

        note = _find_note_payload(state)
        if note is None:
            return XiaohongshuNote(
                title=None, content="", author=None, published_at=None,
                source_url=source_url, note_id=note_id,
            )

        title = _as_text(note.get("title"))
        description = _as_text(note.get("desc"))
        author = None
        user = note.get("user")
        if isinstance(user, dict):
            author = _as_text(user.get("nickname"))

        published_at = None
        for key in ("time", "lastUpdateTime", "publishTime"):
            value = note.get(key)
            if isinstance(value, (int, float)) and value > 0:
                published_at = _format_epoch(int(value))
                break

        return XiaohongshuNote(
            title=title,
            content=description,
            author=author,
            published_at=published_at,
            source_url=source_url,
            note_id=note_id,
        )


def _extract_initial_state(html: str) -> dict[str, Any] | None:
    """从 HTML 中取出并解析 `__INITIAL_STATE__`。

    两个难点：

    1. 小红书的初始状态含 JS 字面量 `undefined`，标准 JSON 解析器不接受。
       替换时必须避开字符串里的内容——笔记正文出现 "undefined" 这样的
       普通单词很常见，误替换会改坏用户原文。
    2. 状态是嵌套 JSON，非贪婪匹配会在中途的 `}}` 处截断。这里先按
       括号配对找出完整对象，再做 JSON 解析。
    """
    start = html.find("__INITIAL_STATE__")
    if start < 0:
        return None
    # 状态以 `__INITIAL_STATE__={` 开头。要跳过标识符与等号，定位真正的
    # 对象起始位置——直接找第一个 `{` 会误命中字符串内容里的花括号。
    equals = _find_assignment(html, start + len("__INITIAL_STATE__"))
    if equals < 0:
        return None
    brace = html.find("{", equals)
    if brace < 0:
        return None
    # 等号与花括号之间只允许空白。
    if html[equals + 1 : brace].strip():
        return None

    raw = _extract_balanced_object(html, brace)
    if raw is None:
        return None

    normalized = _replace_js_undefined(raw)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _find_assignment(text: str, start: int) -> int:
    """从 start 起找到赋值用的等号位置；找不到返回 -1。

    只跳过空白字符，避免把标识符里的等号或字符串内容当成赋值号。
    """
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        return index if char == "=" else -1
    return -1


def _extract_balanced_object(text: str, start: int) -> str | None:
    """从 start 处的 `{` 起，按括号配对取出完整 JSON 对象。

    字符串里的括号不参与配对，否则正文中的引号会让配对提前结束。
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _replace_js_undefined(raw: str) -> str:
    """把 JSON 结构中的 JS 字面量 `undefined` 换成 `null`。

    只替换结构位置的值：跳过字符串内部，也不碰标识符的一部分
    （如 `undefinedValue`）。字符串里的 "undefined" 属于用户原文，不能动。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if raw.startswith("undefined", index):
            before = raw[index - 1] if index > 0 else ""
            after = raw[index + 9] if index + 9 < length else ""
            # 前后不是标识符字符时才是独立的字面量。
            if not (before.isalnum() or before in "_$") and not (
                after.isalnum() or after in "_$"
            ):
                out.append("null")
                index += 9
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _find_note_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    """在初始状态里定位笔记主体。

    桌面端结构是 `note.noteDetailMap[<id>].note`，移动端是
    `noteData.currentNote`/`preLoaderData`。两处都试，取到即用。
    """
    note_section = state.get("note")
    if isinstance(note_section, dict):
        detail_map = note_section.get("noteDetailMap")
        if isinstance(detail_map, dict):
            for entry in detail_map.values():
                if not isinstance(entry, dict):
                    continue
                note = entry.get("note")
                if isinstance(note, dict):
                    return note

    note_data = state.get("noteData")
    if isinstance(note_data, dict):
        for key in ("currentNote", "preLoaderData"):
            candidate = note_data.get(key)
            if isinstance(candidate, dict):
                note = candidate.get("note")
                if isinstance(note, dict):
                    return note
                if key == "currentNote":
                    return candidate

    return None


def _as_text(value: Any) -> str | None:
    """把可能是 str/None 的值规整为文本；空串返回 None。"""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _format_epoch(seconds: int) -> str:
    """Unix 秒转 ISO 8601（带 +08:00 偏移）。

    小红书返回的是秒级时间戳。平台展示时区为 Asia/Shanghai。
    """
    from datetime import datetime, timedelta, timezone

    shanghai = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(seconds, tz=shanghai).isoformat()


def _is_official_xhs_url(url: str) -> bool:
    """判断 URL 是否指向官方 host。短链跳转的每一跳都要过这一关。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = parts.netloc.lower()
    if ":" in host:
        host, _, port = host.rpartition(":")
        if port not in ("443", ""):
            return False
    return host in NOTE_HOSTS or host in SHORT_LINK_HOSTS


def _join_url(base: str, location: str) -> str:
    """拼接相对跳转地址。"""
    from urllib.parse import urljoin

    return urljoin(base, location)

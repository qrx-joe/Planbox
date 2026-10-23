"""T-009 小红书只读适配器定向回归。

只收集本文件，禁用外网，SQLite 使用独立临时目录。
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.services import wechat_fetcher
from backend.app.services.xiaohongshu_fetcher import (
    MAX_DECOMPRESSED_BYTES,
    MAX_WIRE_BYTES,
    XiaohongshuFetcherError,
    XiaohongshuNoteFetcher,
    extract_note_id,
    is_xiaohongshu_note_url,
    is_xiaohongshu_short_link,
)

NOTE_ID = "6a8c47f1000000002202378d"
NOTE_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
SHORT_URL = "https://xhslink.com/m/2b9DgUmzS5D"
RESOLVED_URL = (
    "https://www.xiaohongshu.com/user/profile/67acb145000000000e01e020"
    "?xsec_token=YBuLlsoUnkZbYDL6DMrIG1AE23MbTg23oCxcu3Si0RSYQ=&xsec_source=app_share"
)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("定向测试禁止外网请求")

    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def isolated_dir():
    root = PROJECT_ROOT / ".testdata"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t009-", dir=root) as directory:
        yield Path(directory)


def response(status=200, *, text="", headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update({"Content-Type": "text/html; charset=utf-8"})
    result.headers.update(headers or {})
    result._content = text.encode("utf-8")
    result._content_consumed = True
    result.encoding = "utf-8"
    return result


def note_state(note_id=NOTE_ID, **overrides):
    """构造与真实页面结构一致的内嵌状态。"""
    note = {
        "title": "Jetson 暑期训练营全回顾",
        "desc": "8 月 1 日开营，8 月 31 日结课。四周时间，掌握 Jetson 边缘开发。",
        "user": {"nickname": "Seeed Studio"},
        "time": 1755000000,
        "imageList": [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/a.jpg"}],
    }
    note.update(overrides)
    return {"note": {"noteDetailMap": {note_id: {"note": note}}}}


def state_html(state):
    """把状态包成页面 HTML，含 JS 字面量 undefined（真实页面特征）。"""
    raw = json.dumps(state, ensure_ascii=False)
    return (
        "<html><script>window.__INITIAL_STATE__="
        + raw.replace("undefined", "null")
        + "</script></html>"
    )


# --- 链接校验 ---


@pytest.mark.parametrize(
    "url",
    [
        NOTE_URL,
        f"{NOTE_URL}?xsec_token=ABC=&xsec_source=app_share",
        f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}",
        f"https://xiaohongshu.com/explore/{NOTE_ID}",
        f"https://www.xiaohongshu.com:443/explore/{NOTE_ID}",
    ],
)
def test_note_urls_are_accepted(url):
    assert is_xiaohongshu_note_url(url)


@pytest.mark.parametrize(
    "url",
    [
        # 用户主页与合集页不是单篇笔记。
        "https://www.xiaohongshu.com/user/profile/668ccf28000000000303311d",
        "https://www.xiaohongshu.com/collection/abc123",
        "https://www.xiaohongshu.com/explore/notahexid",
        "https://www.xiaohongshu.com/explore/6a8c47f1000000002202378",
        "http://www.xiaohongshu.com/explore/6a8c47f1000000002202378d",
        "https://evil.com/explore/6a8c47f1000000002202378d",
        "https://www.xiaohongshu.com.evil.com/explore/6a8c47f1000000002202378d",
        "https://www.xiaohongshu.com:8443/explore/6a8c47f1000000002202378d",
        "https://user@www.xiaohongshu.com/explore/6a8c47f1000000002202378d",
        "https://mp.weixin.qq.com/s/Abc_123",
        "",
    ],
)
def test_non_note_urls_are_rejected(url):
    assert not is_xiaohongshu_note_url(url)


def test_short_link_accepted_only_for_official_host():
    assert is_xiaohongshu_short_link(SHORT_URL)
    assert not is_xiaohongshu_short_link("https://evil.com/xhslink.com")
    assert not is_xiaohongshu_short_link("http://xhslink.com/m/abc")
    assert not is_xiaohongshu_short_link("https://xhslink.com/")


def test_note_id_extraction():
    assert extract_note_id(NOTE_URL) == NOTE_ID
    assert extract_note_id("https://www.xiaohongshu.com/user/profile/abc") is None


# --- 短链解析 ---


def test_short_link_resolves_to_official_url_keeping_token(monkeypatch):
    """短链的 302 Location 带 xsec_token，是取内容的必要参数，不能丢。"""
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session, "get", Mock(return_value=response(302, headers={"Location": RESOLVED_URL}))
    )
    resolved = fetcher.resolve(SHORT_URL)
    assert resolved.startswith("https://www.xiaohongshu.com/")
    assert "xsec_token=" in resolved


def test_short_link_follows_only_one_hop(monkeypatch):
    """只跟一跳。

    官方短链的第一跳就指向带 token 的目标页；再往下会进入 /login 登录页，
    反而丢掉 token。跟多跳会被登录页的 HTML 误导出错误的地址。
    """
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    get = Mock(return_value=response(302, headers={"Location": RESOLVED_URL}))
    monkeypatch.setattr(fetcher.session, "get", get)
    fetcher.resolve(SHORT_URL)
    assert get.call_count == 1


def test_short_link_redirect_to_foreign_host_is_rejected(monkeypatch):
    """短链跳转到非官方域名必须拒绝，不能跟随。"""
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session,
        "get",
        Mock(return_value=response(302, headers={"Location": "https://evil.com/x"})),
    )
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.resolve(SHORT_URL)


def test_short_link_without_location_is_rejected(monkeypatch):
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=response(302)))
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.resolve(SHORT_URL)


def test_non_xhs_url_is_rejected_before_request(monkeypatch):
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    get = Mock()
    monkeypatch.setattr(fetcher.session, "get", get)
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.resolve("https://example.com/a")
    get.assert_not_called()


# --- 正文解析 ---


def test_note_content_is_extracted_from_initial_state(monkeypatch):
    """从内嵌状态取出标题、正文、作者与发布时间。"""
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session, "get", Mock(return_value=response(text=state_html(note_state())))
    )
    note = fetcher.fetch(NOTE_URL)
    assert note["title"] == "Jetson 暑期训练营全回顾"
    assert "8 月 1 日开营" in note["content"]
    assert note["author"] == "Seeed Studio"
    assert note["published_at"] == "2025-08-12T20:00:00+08:00"
    assert note["note_id"] == NOTE_ID


def test_undefined_literal_does_not_break_parsing(monkeypatch):
    """真实页面的初始状态含 JS 字面量 undefined，标准 JSON 解析会失败。

    同时验证字符串里的 "undefined" 不被误替换——笔记正文出现这个普通
    单词很常见，误替换会改坏用户原文。
    """
    state = {
        "note": {
            "noteDetailMap": {
                NOTE_ID: {
                    "note": {
                        "title": "含 undefined 的笔记",
                        "desc": "正文内容在此",
                        "jsAssetsList": None,
                    }
                }
            }
        }
    }
    # 手工拼出 JS 字面量 undefined（json.dumps 会写成 null）。
    raw = (
        '{"note":{"noteDetailMap":{"' + NOTE_ID + '":{"note":'
        '{"title":"含 undefined 的笔记","desc":"正文内容在此",'
        '"jsAssetsList":undefined}}}}}'
    )
    html = "<html><script>window.__INITIAL_STATE__=" + raw + "</script></html>"
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=response(text=html)))
    note = fetcher.fetch(NOTE_URL)
    assert note["title"] == "含 undefined 的笔记"
    assert note["content"] == "正文内容在此"
    # 确认状态确实被解析到，而不是走了空内容分支。
    assert state["note"]["noteDetailMap"][NOTE_ID]["note"]["title"]


def test_missing_state_degrades_to_empty_content(monkeypatch):
    """页面没有初始状态时返回空正文，不抛错——调用方据此引导用户粘贴。"""
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session, "get", Mock(return_value=response(text="<html><body>骨架页</body></html>"))
    )
    note = fetcher.fetch(NOTE_URL)
    assert note["content"] == ""
    assert note["title"] is None
    assert note["note_id"] == NOTE_ID


def test_empty_note_map_degrades_to_empty_content(monkeypatch):
    """风控或未登录时 noteDetailMap 为空，应降级而非报错。

    真实样本：裸笔记 ID 请求会返回 noteDetailMap 为空的骨架页。
    """
    html = state_html({"note": {"noteDetailMap": {}}})
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=response(text=html)))
    note = fetcher.fetch(NOTE_URL)
    assert note["content"] == ""


def test_non_html_response_is_rejected(monkeypatch):
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session,
        "get",
        Mock(return_value=response(headers={"Content-Type": "application/json"})),
    )
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.fetch(NOTE_URL)


def test_declared_content_length_over_limit_is_rejected(monkeypatch):
    """声明长度超限要在读正文前拒绝。"""
    result = response(headers={"Content-Length": str(MAX_WIRE_BYTES + 1)})
    monkeypatch.setattr(result, "iter_content", Mock(side_effect=AssertionError("不应读取正文")))
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=result))
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.fetch(NOTE_URL)
    result.iter_content.assert_not_called()


def test_decompressed_body_has_a_hard_ceiling(monkeypatch):
    """解压后体积仍有硬上限，不能靠高压缩比撑大内存。"""
    html = state_html({"note": {"noteDetailMap": {NOTE_ID: {"note": {
        "title": "x" * (MAX_DECOMPRESSED_BYTES + 1024 * 1024), "desc": "d"}}}}})
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=response(text=html)))
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.fetch(NOTE_URL)


def test_unbalanced_state_degrades_to_empty_content(monkeypatch):
    """状态 JSON 括号不配对时应降级为空内容，不能抛错。"""
    raw = '{"note":{"noteDetailMap":{"' + NOTE_ID + '":{"note":'
    html = "<html><script>window.__INITIAL_STATE__=" + raw + "</script></html>"
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=response(text=html)))
    note = fetcher.fetch(NOTE_URL)
    assert note["content"] == ""


def test_network_failure_raises_fetcher_error(monkeypatch):
    fetcher = XiaohongshuNoteFetcher(timeout=3)
    monkeypatch.setattr(
        fetcher.session, "get", Mock(side_effect=requests.ConnectionError())
    )
    with pytest.raises(XiaohongshuFetcherError):
        fetcher.fetch(NOTE_URL)


# --- API 接入 ---


@pytest.fixture
def api_client(monkeypatch, isolated_dir):
    from backend.app.api import share, source_items
    from backend.app.db.database import initialize_database

    monkeypatch.setattr(settings, "data_dir", isolated_dir)
    monkeypatch.setattr(settings, "database_path", isolated_dir / "t009.db")
    monkeypatch.setattr(settings, "upload_dir", isolated_dir / "uploads")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(wechat_fetcher, "_default_fetcher", None)
    initialize_database()
    app = FastAPI()
    app.include_router(source_items.router, prefix="/api/source-items")
    app.include_router(share.router, prefix="/api/share")
    with TestClient(app) as client:
        yield client


def test_fetch_api_accepts_xiaohongshu_note_url(api_client, monkeypatch):
    """小红书笔记链接应进入抓取接口，并建立 source_type=xhs 的条目。"""
    from backend.app.api import source_items

    extract = AsyncMock()
    monkeypatch.setattr(source_items.extraction_service, "extract", extract)
    monkeypatch.setattr(
        requests.Session, "get", Mock(return_value=response(text=state_html(note_state())))
    )
    result = api_client.post("/api/source-items/fetch", json={"source_url": NOTE_URL})
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "ready" and data["fetch_succeeded"]
    assert data["source_item"]["source_type"] == "xhs"
    assert "8 月 1 日开营" in data["source_item"]["raw_text"]
    extract.assert_awaited_once_with(data["source_item"]["id"])


def test_fetch_api_accepts_xiaohongshu_short_link(api_client, monkeypatch):
    """短链也应进入抓取接口；解析出的完整链接记在 source_url。"""
    from backend.app.api import source_items

    extract = AsyncMock()
    monkeypatch.setattr(source_items.extraction_service, "extract", extract)
    monkeypatch.setattr(
        requests.Session,
        "get",
        Mock(return_value=response(302, headers={"Location": RESOLVED_URL})),
    )
    result = api_client.post("/api/source-items/fetch", json={"source_url": SHORT_URL})
    assert result.status_code == 200
    data = result.json()
    # 短链指向主页，抓不到正文属预期，应降级而不是报错。
    assert data["status"] == "needs_content"
    assert data["source_item"]["source_type"] == "xhs"
    assert data["resolved_url"].startswith("https://www.xiaohongshu.com/")
    assert "xsec_token=" in data["resolved_url"]
    assert not data["extraction_started"]


def test_fetch_api_rejects_unrelated_url(api_client):
    """既不是微信也不是小红书的链接应被拒绝，不建条目。"""
    result = api_client.post("/api/source-items/fetch", json={"source_url": "https://example.com/a"})
    assert result.status_code == 400
    assert api_client.get("/api/source-items").json() == []


def test_fetch_api_spoofed_host_is_rejected(api_client, monkeypatch):
    """伪装 host 不得触发抓取。"""
    get = Mock()
    monkeypatch.setattr(requests.Session, "get", get)
    result = api_client.post(
        "/api/source-items/fetch",
        json={"source_url": "https://evil.com/explore/6a8c47f1000000002202378d"},
    )
    assert result.status_code == 400
    get.assert_not_called()


def test_failed_xhs_fetch_still_creates_item_for_manual_completion(api_client, monkeypatch):
    """抓不到正文也要建条目，否则用户粘贴的内容无处安放。

    粘贴补全走的是与微信共用的 /api/share/{id}/complete，门槛为 50 字。
    """
    from backend.app.api import source_items

    extract = AsyncMock()
    monkeypatch.setattr(source_items.extraction_service, "extract", extract)
    monkeypatch.setattr(
        requests.Session, "get", Mock(side_effect=requests.Timeout())
    )
    result = api_client.post("/api/source-items/fetch", json={"source_url": NOTE_URL})
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "needs_content"
    assert not data["fetch_succeeded"] and not data["extraction_started"]
    assert data["next_action"]
    extract.assert_not_awaited()

    item_id = data["source_item"]["id"]
    completed = api_client.post(
        f"/api/share/{item_id}/complete",
        json={
            "raw_text": (
                "手工粘贴的小红书笔记正文：活动名称、举办时间、地点、报名方式与截止日期"
                "都写在这里，字数需要满足补全接口的门槛要求。"
            ),
            "auto_extract": False,
        },
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "ready"

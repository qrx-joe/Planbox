"""T-001 定向回归：只收集本文件，禁用外网，SQLite 使用独立临时目录。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.config import PROJECT_ROOT, Settings, settings
from backend.app.services import wechat_fetcher
from backend.app.services.wechat_fetcher import (
    WechatArticleFetcher,
    WechatFetcherError,
    is_wechat_url,
)

SHORT_URL = "https://mp.weixin.qq.com/s/Abc_123-xyz"
LONG_URL = "https://mp.weixin.qq.com/s?__biz=MzA%2B%2F%3D&mid=123&idx=1&sn=abc#rd"
BODY = "这是一段合成活动正文，用于确认抓取成功与人工补全路径。" * 4
HTML = f'<title>合成活动</title><div id="js_content"><p>{BODY}</p></div>'


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("定向测试禁止外网请求")

    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def isolated_dir():
    root = PROJECT_ROOT / ".testdata"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t001-", dir=root) as directory:
        yield Path(directory)


def response(status=200, *, text=HTML, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update({"Content-Type": "text/html; charset=utf-8"})
    result.headers.update(headers or {})
    result._content = text.encode("utf-8")
    result._content_consumed = True
    result.encoding = "utf-8"
    return result


@pytest.mark.parametrize(
    "url",
    [
        SHORT_URL,
        LONG_URL,
        SHORT_URL.replace("Abc", "%41bc"),
        SHORT_URL.replace("mp.weixin.qq.com", "MP.WEIXIN.QQ.COM:443"),
    ],
)
def test_standard_urls_reach_request_branch(monkeypatch, url):
    fetcher = WechatArticleFetcher(timeout=3)
    get = Mock(return_value=response())
    monkeypatch.setattr(fetcher.session, "get", get)
    assert is_wechat_url(url)
    article = fetcher.fetch(url)
    assert article["title"] == "合成活动"
    assert article["content"] == BODY
    assert article["source_url"] == url
    get.assert_called_once_with(url, timeout=3, allow_redirects=False, stream=True)


INVALID_URLS = [
    "https://mp.weixin.qq.com.evil.example/s/abc",
    "https://evil.example/s/abc?url=mp.weixin.qq.com",
    "https://mp.weixin.qq.com@127.0.0.1/s/abc",
    "https://user@mp.weixin.qq.com/s/abc",
    "http://mp.weixin.qq.com/s/abc",
    "ftp://mp.weixin.qq.com/s/abc",
    "//mp.weixin.qq.com/s/abc",
    "mp.weixin.qq.com/s/abc",
    "https://mp.weixin.qq.com:8080/s/abc",
    "https://mp.weixin.qq.com:bad/s/abc",
    "https://mp%2Eweixin.qq.com/s/abc",
    "https://mp.weixin.qq.com./s/abc",
    "https://mp.weixin.qq.com\\@evil.example/s/abc",
    "https://mp.weixin.qq.com/\ns/abc",
    "https://mp.weixin.qq.com/mp/profile_ext?action=home",
    "https://mp.weixin.qq.com/s/",
    "https://mp.weixin.qq.com/s",
    "https://mp.weixin.qq.com/s?__biz=abc",
    "https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=",
    "https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&idx=",
    "https://mp.weixin.qq.com/s/abc%2Fdef",
    "https://mp.weixin.qq.com/s/abc%ZZ",
    "https://mp.weixin.qq.com/s/abc?x=%",
    quote(SHORT_URL, safe=""),
]


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_urls_are_rejected_before_network(monkeypatch, url):
    fetcher = WechatArticleFetcher()
    get = Mock()
    monkeypatch.setattr(fetcher.session, "get", get)
    assert not is_wechat_url(url)
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(url)
    get.assert_not_called()


def test_same_host_redirect_is_followed_without_decoding_query(monkeypatch):
    fetcher = WechatArticleFetcher()
    target = LONG_URL.split("mp.weixin.qq.com", 1)[1]
    first = response(302, headers={"Location": target})
    get = Mock(side_effect=[first, response()])
    monkeypatch.setattr(fetcher.session, "get", get)
    assert fetcher.fetch(SHORT_URL)["source_url"] == SHORT_URL
    assert [call.args[0] for call in get.call_args_list] == [SHORT_URL, LONG_URL]
    assert all(call.kwargs["allow_redirects"] is False for call in get.call_args_list)


@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/s/x",
        "https://evil.example/s/x",
        "//mp.weixin.qq.com.evil.example/s/x",
        "http://mp.weixin.qq.com/s/x",
        "/mp/profile_ext",
        "",
        "https://[invalid/s/x",
        "/s/\nabc",
    ],
)
def test_unsafe_redirect_is_not_requested(monkeypatch, target):
    fetcher = WechatArticleFetcher()
    get = Mock(return_value=response(302, headers={"Location": target}))
    monkeypatch.setattr(fetcher.session, "get", get)
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(SHORT_URL)
    assert get.call_count == 1


def test_redirect_loop_is_bounded(monkeypatch):
    fetcher = WechatArticleFetcher()
    get = Mock(return_value=response(302, headers={"Location": SHORT_URL}))
    monkeypatch.setattr(fetcher.session, "get", get)
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(SHORT_URL)
    assert get.call_count == 6


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_network_failure_stops_without_retry_or_profile_fallback(monkeypatch, failure):
    fetcher = WechatArticleFetcher()
    get = Mock(side_effect=failure)
    monkeypatch.setattr(fetcher.session, "get", get)
    with pytest.raises(WechatFetcherError) as error:
        fetcher.fetch(LONG_URL)
    assert LONG_URL not in str(error.value)
    assert "粘贴" in str(error.value)
    assert get.call_count == 1


@pytest.mark.parametrize(
    "result",
    [
        response(403),
        response(text="<title>访问受限</title>"),
        response(headers={"Content-Type": "application/json"}),
        response(headers={"Content-Length": str(2 * 1024 * 1024 + 1)}),
        response(text=HTML + "x" * (2 * 1024 * 1024)),
    ],
)
def test_http_body_boundaries_fail_clearly(monkeypatch, result):
    fetcher = WechatArticleFetcher()
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=result))
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(SHORT_URL)


def test_oversized_header_stops_before_body_and_closes_response(monkeypatch):
    result = response(headers={"Content-Length": str(2 * 1024 * 1024 + 1)})
    monkeypatch.setattr(result, "iter_content", Mock(side_effect=AssertionError("不应读取正文")))
    close = Mock(wraps=result.close)
    monkeypatch.setattr(result, "close", close)
    fetcher = WechatArticleFetcher()
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=result))
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(SHORT_URL)
    result.iter_content.assert_not_called()
    close.assert_called_once()


def test_invalid_charset_is_a_fetch_error(monkeypatch):
    result = response(headers={"Content-Type": "text/html; charset=not-a-codec"})
    result.encoding = "not-a-codec"
    fetcher = WechatArticleFetcher()
    monkeypatch.setattr(fetcher.session, "get", Mock(return_value=result))
    with pytest.raises(WechatFetcherError):
        fetcher.fetch(SHORT_URL)


@pytest.fixture
def api_client(monkeypatch, isolated_dir):
    from backend.app.api import share, source_items
    from backend.app.db.database import initialize_database

    monkeypatch.setattr(settings, "data_dir", isolated_dir)
    monkeypatch.setattr(settings, "database_path", isolated_dir / "t001.db")
    monkeypatch.setattr(settings, "upload_dir", isolated_dir / "uploads")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(wechat_fetcher, "_default_fetcher", None)
    initialize_database()
    app = FastAPI()
    app.include_router(source_items.router, prefix="/api/source-items")
    app.include_router(share.router, prefix="/api/share")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("url", INVALID_URLS[:10])
def test_fetch_api_rejects_bad_urls_without_saving(api_client, url):
    result = api_client.post("/api/source-items/fetch", json={"source_url": url})
    assert result.status_code == 400
    assert api_client.get("/api/source-items").json() == []


@pytest.mark.parametrize("url", INVALID_URLS[:4])
def test_share_does_not_fetch_spoofed_hosts(api_client, monkeypatch, url):
    get = Mock()
    monkeypatch.setattr(requests.Session, "get", get)
    result = api_client.post("/api/share/intake", json={"article_url": url})
    assert result.status_code == 200
    data = result.json()
    assert not data["is_wechat_article"] and not data["fetch_attempted"]
    assert data["state"] == "needs_content"
    get.assert_not_called()


def test_fetch_api_success_reaches_extraction_boundary(api_client, monkeypatch):
    from backend.app.api import source_items

    extract = AsyncMock()
    monkeypatch.setattr(source_items.extraction_service, "extract", extract)
    get = Mock(return_value=response())
    monkeypatch.setattr(requests.Session, "get", get)
    result = api_client.post("/api/source-items/fetch", json={"source_url": SHORT_URL})
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "ready" and data["fetch_succeeded"]
    assert data["source_item"]["raw_text"] == BODY
    assert data["extraction_started"]
    extract.assert_awaited_once_with(data["source_item"]["id"])
    assert get.call_count == 1


@pytest.mark.parametrize("entry", ["fetch", "share"])
def test_failed_fetch_can_be_completed_without_model_call(api_client, monkeypatch, entry):
    get = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(requests.Session, "get", get)
    if entry == "fetch":
        result = api_client.post("/api/source-items/fetch", json={"source_url": SHORT_URL})
        assert result.status_code == 200
        data = result.json()
        item_id = data["source_item"]["id"]
        assert data["status"] == "needs_content"
    else:
        result = api_client.post("/api/share/intake", json={"article_url": SHORT_URL})
        assert result.status_code == 200
        data = result.json()
        item_id = data["source_item_id"]
        assert data["state"] == "needs_content" and data["fetch_attempted"]
    assert not data["fetch_succeeded"] and not data["extraction_started"]
    assert data["next_action"]
    assert get.call_count == 1
    result = api_client.post(
        f"/api/share/{item_id}/complete", json={"raw_text": BODY, "auto_extract": False}
    )
    assert result.status_code == 200
    assert result.json()["state"] == "ready"
    item = api_client.get(f"/api/source-items/{item_id}").json()
    assert item["source_url"] == SHORT_URL and item["raw_text"] == BODY
    assert item["ingest_state"] == "ready"


def test_share_encoded_query_reaches_fetcher(api_client, monkeypatch):
    from backend.app.api import share

    monkeypatch.setattr(share.service, "_extract", AsyncMock(return_value=False))
    get = Mock(return_value=response())
    monkeypatch.setattr(requests.Session, "get", get)
    result = api_client.post("/api/share/intake", json={"query": "url=" + quote(LONG_URL, safe="")})
    assert result.status_code == 200
    data = result.json()
    assert data["is_wechat_article"] and data["fetch_succeeded"]
    assert data["state"] == "ready" and not data["extraction_started"]
    assert get.call_args.args[0] == LONG_URL


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ('["https://a.example", "https://b.example"]', ["https://a.example", "https://b.example"]),
        ("https://single.example", ["https://single.example"]),
        (" , ", ["http://localhost:5173"]),
        ("[]", []),
    ],
)
def test_cors_environment_formats(monkeypatch, value, expected):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", value)
    assert Settings(_env_file=None).cors_allow_origins == expected


def test_cors_defaults_and_python_list(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_allow_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    assert Settings(
        _env_file=None, cors_allow_origins=["https://a.example"]
    ).cors_allow_origins == [
        "https://a.example",
    ]


def test_cors_dotenv_and_invalid_json(monkeypatch, isolated_dir):
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    env_file = isolated_dir / "cors.env"
    env_file.write_text(
        'CORS_ALLOW_ORIGINS="https://a.example,https://b.example"\n', encoding="utf-8"
    )
    assert Settings(_env_file=env_file).cors_allow_origins == [
        "https://a.example",
        "https://b.example",
    ]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allow_origins='["broken"')


@pytest.mark.parametrize(
    "value", ["https://a.example,https://b.example", '["https://a.example", "https://b.example"]']
)
def test_cors_actual_app_startup_and_preflight(isolated_dir, value):
    env = os.environ.copy()
    env.update(
        CORS_ALLOW_ORIGINS=value,
        LLM_PROVIDER="fake",
        DATA_DIR=str(isolated_dir),
        DATABASE_PATH=str(isolated_dir / "startup.db"),
        UPLOAD_DIR=str(isolated_dir / "uploads"),
    )
    script = """
import json
from fastapi.testclient import TestClient
from backend.app.main import app
with TestClient(app) as client:
    health = client.get('/api/health')
    assert health.status_code == 200
    assert health.json()['env']['cors_origins'] == ['https://a.example', 'https://b.example']
    for origin in ['https://a.example', 'https://b.example', 'https://blocked.example']:
        result = client.options('/api/source-items/fetch', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST'})
        if origin == 'https://blocked.example':
            assert result.status_code == 400
            assert 'access-control-allow-origin' not in result.headers
        else:
            assert result.status_code == 200
            assert result.headers['access-control-allow-origin'] == origin
    print(json.dumps({'startup': 'ok', 'preflights': 3}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, "隔离启动/CORS 预检失败；未输出可能含本地配置的子进程日志"
    assert json.loads(result.stdout) == {"startup": "ok", "preflights": 3}

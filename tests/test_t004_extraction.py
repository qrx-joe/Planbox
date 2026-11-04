"""T-004 抽取入口回归：显式收集本文件，临时 SQLite，禁止真实模型/外网。"""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import httpx
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.core.time import now_iso, now_shanghai
from backend.app.db.database import connect, initialize_database
from backend.app.providers.llm import BaseLLMProvider, OpenAIProvider
from backend.app.repositories.source_item_repository import SourceItemRepository
from backend.app.schemas.extraction import ExtractedEventDraft
from backend.app.services.extraction_service import ExtractionService

BODY = "合成活动正文，欢迎参加技术交流。本段仅用于验证抽取的状态和重试。" * 4
WECHAT_URL = "https://mp.weixin.qq.com/s/synthetic"
XHS_URL = "https://www.xiaohongshu.com/explore/65aa00000000000000000001"
SAFE_ERROR = "模型服务故障：secret-test-key / 私人正文（不得返回）"


class ControlledProvider(BaseLLMProvider):
    """可控成功、失败和暂停点；读取真实数据库观察调用时的解析状态。"""

    def __init__(self):
        self.calls = 0
        self.error = None
        self.empty_title = False
        self.started = Event()
        self.release = None
        self.observed_states = []

    async def extract_event(self, text, url=None):
        self.calls += 1
        with connect() as connection:
            row = connection.execute(
                "SELECT parse_status FROM source_items ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            self.observed_states.append(row["parse_status"])
        self.started.set()
        if self.release is not None:
            assert await asyncio.to_thread(self.release.wait, 5), "测试未释放模型暂停点"
        if self.error is not None:
            raise self.error
        return ExtractedEventDraft(
            title="" if self.empty_title else "合成活动",
            organizer=None if self.empty_title else "合成主办方")


@pytest.fixture
def api(monkeypatch):
    from backend.app.api import events as events_api
    from backend.app.api import review, share, source_items

    def forbidden(*args, **kwargs):
        raise AssertionError("T-004 定向测试禁止外网")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    root = PROJECT_ROOT / ".testdata"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t004-", dir=root) as directory:
        path = Path(directory)
        monkeypatch.setattr(settings, "database_path", path / "extraction.db")
        monkeypatch.setattr(settings, "data_dir", path)
        monkeypatch.setattr(settings, "upload_dir", path / "uploads")
        initialize_database()
        provider = ControlledProvider()
        monkeypatch.setattr(ExtractionService, "_get_llm_provider", lambda self: provider)

        async def fetched(url):
            return {"title": "合成来源", "content": BODY, "source_url": url}

        monkeypatch.setattr(source_items, "fetch_wechat_article", fetched)
        monkeypatch.setattr(source_items, "_fetch_xhs_async", lambda fetcher, url: fetched(url))
        monkeypatch.setattr(share.service, "_try_fetch", fetched)
        app = FastAPI()
        app.include_router(source_items.router, prefix="/api/source-items")
        app.include_router(share.router, prefix="/api/share")
        app.include_router(review.router, prefix="/api/review-queue")
        app.include_router(events_api.router, prefix="/api/events")
        with TestClient(app, raise_server_exceptions=False) as client:
            client.provider = provider
            yield client


def source(api, **values):
    result = api.post("/api/source-items", json={"raw_text": BODY, **values})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def current(api, source_id):
    return api.get(f"/api/source-items/{source_id}").json()


def counts():
    with connect() as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("extraction_results", "evidences", "events"))


def enter(api, entry):
    if entry == "manual":
        source_id = source(api)
        result = api.post(f"/api/source-items/{source_id}/extract")
    elif entry in {"wechat", "xhs"}:
        result = api.post("/api/source-items/fetch", json={
            "source_url": WECHAT_URL if entry == "wechat" else XHS_URL,
        })
        source_id = result.json()["source_item"]["id"]
    elif entry in {"intake", "intake_fetch"}:
        result = api.post("/api/share/intake", json=(
            {"summary": BODY} if entry == "intake" else {"article_url": WECHAT_URL}
        ))
        source_id = result.json()["source_item_id"]
    else:
        source_id = api.post("/api/share/intake", json={}).json()["source_item_id"]
        result = api.post(f"/api/share/{source_id}/complete", json={"raw_text": BODY})
    return source_id, result


@pytest.mark.parametrize("entry", ["manual", "wechat", "xhs", "intake", "intake_fetch", "complete"])
@pytest.mark.parametrize("fails", [False, True])
def test_all_entries_success_failure_and_manual_retry(api, entry, fails):
    if fails:
        api.provider.error = RuntimeError(SAFE_ERROR)
    source_id, result = enter(api, entry)
    assert result.status_code == (502 if entry == "manual" and fails else 200)
    item = current(api, source_id)
    assert item["parse_status"] == ("failed" if fails else "parsed")
    assert api.provider.observed_states == ["pending"]
    assert api.provider.calls == 1
    assert counts()[0] == (0 if fails else 1)
    if entry != "manual":
        assert result.json()["extraction_started"] is not fails
    if entry in {"wechat", "xhs"}:
        assert result.json()["source_item"] == item
        assert result.json()["fetch_succeeded"]
    if fails:
        assert item["parse_error"] and SAFE_ERROR not in str(item)
        assert SAFE_ERROR not in result.text
        if entry in {"wechat", "xhs", "intake", "intake_fetch"}:
            assert "已生成" not in result.json()["next_action"]
    else:
        assert item["parse_error"] is None
    api.provider.error = None
    retry = api.post(f"/api/source-items/{source_id}/extract")
    assert retry.status_code == 200, retry.text
    assert counts()[0] == 1
    assert api.provider.calls == (2 if fails else 1)
    before = counts()
    lost_response_retry = api.post(f"/api/source-items/{source_id}/extract")
    assert lost_response_retry.json()["id"] == retry.json()["id"]
    assert counts() == before and current(api, source_id)["parse_status"] == "parsed"


@pytest.mark.parametrize("state", ["unparsed", "pending", "parsed", "failed", "unknown"])
def test_manual_state_gate(api, state):
    source_id = source(api)
    SourceItemRepository().update_parse_status(source_id, state, now_iso(), "旧错误")
    response = api.post(f"/api/source-items/{source_id}/extract")
    expected = 200 if state in {"unparsed", "failed"} else 409
    assert response.status_code == expected, response.text
    assert api.provider.calls == (1 if expected == 200 else 0)
    if expected == 200:
        assert current(api, source_id)["parse_status"] == "parsed"
        assert current(api, source_id)["parse_error"] is None
    assert counts()[0] == (1 if expected == 200 else 0)


@pytest.mark.parametrize("entry", ["manual", "complete"])
@pytest.mark.parametrize("status", ["pending_review", "confirmed", "rejected"])
def test_retry_preserves_existing_review_state_and_revision(api, entry, status):
    source_id = source(api)
    first = api.post(f"/api/source-items/{source_id}/extract").json()
    edited = api.patch(f"/api/review-queue/{first['id']}", json={
        "changes": {"title": "人工核对后的标题"}, "expected_revision": 0,
    })
    assert edited.status_code == 200, edited.text
    if status == "confirmed":
        assert api.post(f"/api/review-queue/{first['id']}/confirm", json={}).status_code == 200
    elif status == "rejected":
        assert api.post(f"/api/review-queue/{first['id']}/reject").status_code == 204
    before = counts()
    response = api.post(f"/api/source-items/{source_id}/extract") if entry == "manual" else (
        api.post(f"/api/share/{source_id}/complete", json={"raw_text": BODY})
    )
    assert response.status_code == 200, response.text
    if entry == "manual":
        assert response.json()["id"] == first["id"]
        assert response.json()["status"] == status
        assert response.json()["revision"] == 1
        assert response.json()["extracted"]["title"] == "人工核对后的标题"
    else:
        assert not response.json()["extraction_started"]
        assert response.json()["extraction_id"] == first["id"]
    assert current(api, source_id)["parse_status"] == "parsed"
    assert counts() == before and api.provider.calls == 1


@pytest.mark.parametrize("auto_extract", [False, True])
@pytest.mark.parametrize("state", ["unparsed", "pending", "parsed", "failed"])
def test_same_content_completion_preserves_state_or_retries(api, auto_extract, state):
    source_id = source(api)
    first = api.post(f"/api/source-items/{source_id}/extract").json()
    SourceItemRepository().update_parse_status(source_id, state, now_iso(), "旧错误")
    result = api.post(f"/api/share/{source_id}/complete", json={
        "raw_text": BODY, "auto_extract": auto_extract,
    })
    assert result.status_code == 200, result.text
    retries = auto_extract and state in {"unparsed", "failed"}
    assert result.json()["extraction_started"] is retries
    assert current(api, source_id)["parse_status"] == ("parsed" if retries else state)
    assert api.provider.calls == (2 if retries else 1)
    if state == "parsed":
        assert result.json()["extraction_id"] == first["id"]
    elif not retries:
        assert current(api, source_id)["parse_error"] == "旧错误"


def test_same_completion_retries_failed_extraction_and_returns_existing_id(api):
    source_id = source(api)
    url = f"/api/share/{source_id}/complete"
    api.provider.error = RuntimeError(SAFE_ERROR)
    assert api.post(url, json={"raw_text": BODY}).json()["extraction_started"] is False
    assert current(api, source_id)["parse_status"] == "failed"
    api.provider.error = None
    retry = api.post(url, json={"raw_text": BODY}).json()
    assert retry["extraction_started"] and retry["extraction_id"]
    before = counts()
    again = api.post(url, json={"raw_text": BODY}).json()
    assert not again["extraction_started"] and again["extraction_id"] == retry["extraction_id"]
    assert counts() == before and api.provider.calls == 2


@pytest.mark.parametrize("change", [
    {"raw_text": BODY + "新正文"}, {"title": "新来源标题"}, {"published_at": "2027-01-01"},
])
def test_new_content_can_extract_again_without_overwriting_old_draft(api, monkeypatch, change):
    monkeypatch.setattr(
        "backend.app.services.extraction_service.now_iso", lambda: "2026-10-28T12:00:00+08:00",
    )
    source_id = source(api)
    first = api.post(f"/api/source-items/{source_id}/extract").json()
    result = api.post(f"/api/share/{source_id}/complete", json={"raw_text": BODY, **change})
    assert result.status_code == 200, result.text
    assert result.json()["extraction_started"]
    assert result.json()["extraction_id"] != first["id"]
    assert api.get(f"/api/review-queue/{first['id']}").status_code == 200
    assert counts()[0] == 2 and api.provider.calls == 2
    # 同秒写入时也必须返回这次新结果，而非旧结果。
    assert api.post(f"/api/source-items/{source_id}/extract").json()["id"] == (
        result.json()["extraction_id"]
    )


@pytest.mark.parametrize("empty", [None, "", "  \n "])
def test_missing_text_is_rejected_before_claim_or_provider(api, empty):
    source_id = source(api, raw_text=empty, source_url=WECHAT_URL, source_type="screenshot")
    response = api.post(f"/api/source-items/{source_id}/extract")
    assert response.status_code == 400, response.text
    assert current(api, source_id)["parse_status"] == "unparsed"
    assert counts() == (0, 0, 0) and api.provider.calls == 0


def test_missing_source_is_404(api):
    assert api.post("/api/source-items/missing/extract").status_code == 404
    assert api.provider.calls == 0 and counts() == (0, 0, 0)


@pytest.mark.parametrize("failure_point", ["evidences", "parsed"])
def test_persistence_failure_rolls_back_whole_result_then_can_retry(api, failure_point):
    source_id = source(api)
    with connect() as connection:
        sql = ("BEFORE INSERT ON evidences" if failure_point == "evidences" else
               "BEFORE UPDATE ON source_items WHEN NEW.parse_status='parsed'")
        connection.execute(
            f"CREATE TRIGGER fail_extract {sql} BEGIN SELECT RAISE(ABORT, 'synthetic'); END"
        )
        connection.commit()
    response = api.post(f"/api/source-items/{source_id}/extract")
    assert response.status_code == 500
    assert counts() == (0, 0, 0)
    assert current(api, source_id)["parse_status"] == "failed"
    with connect() as connection:
        connection.execute("DROP TRIGGER fail_extract")
        connection.commit()
    assert api.post(f"/api/source-items/{source_id}/extract").status_code == 200
    assert counts()[0] == 1 and api.provider.calls == 2


def test_concurrent_manual_and_completion_do_not_reset_or_duplicate_work(api):
    source_id = source(api)
    api.provider.release = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(api.post, f"/api/source-items/{source_id}/extract")
        try:
            assert api.provider.started.wait(3)
            assert current(api, source_id)["parse_status"] == "pending"
            assert api.post(f"/api/source-items/{source_id}/extract").status_code == 409
            # 独立解释器也必须看见数据库认领，不能依赖进程内锁或路由单例。
            child = subprocess.run([
                sys.executable, "-c", "\n".join([
                    "import asyncio, sys",
                    "from pathlib import Path",
                    "from backend.app.core.config import settings",
                    "from backend.app.services.extraction_service import ExtractionService",
                    "from backend.app.repositories.source_item_repository "
                    "import SourceStateConflict",
                    "settings.database_path = Path(sys.argv[1])",
                    "settings.llm_provider = 'fake'",
                    "try:",
                    "    asyncio.run(ExtractionService().extract(sys.argv[2]))",
                    "except SourceStateConflict:",
                    "    print('conflict')",
                ]), str(settings.database_path), source_id,
            ], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=4)
            assert child.returncode == 0 and child.stdout.strip() == "conflict", child.stderr
            same = api.post(f"/api/share/{source_id}/complete", json={"raw_text": BODY})
            assert same.status_code == 200 and not same.json()["extraction_started"]
            assert current(api, source_id)["parse_status"] == "pending"
            changed = api.post(f"/api/share/{source_id}/complete", json={"raw_text": BODY + "改动"})
            assert changed.status_code == 409
            assert current(api, source_id)["raw_text"] == BODY
        finally:
            api.provider.release.set()
        assert running.result(timeout=5).status_code == 200
    assert api.provider.calls == 1 and counts()[0] == 1


def test_service_itself_owns_lifecycle_and_idempotency(api):
    source_id = source(api)
    first = asyncio.run(ExtractionService().extract(source_id))
    again = asyncio.run(ExtractionService().extract(source_id))
    assert first.id == again.id and api.provider.calls == 1
    assert current(api, source_id)["parse_status"] == "parsed"


@pytest.mark.parametrize("failure", ["timeout", "429", "500", "json", "schema", "envelope"])
def test_real_provider_protocol_failures_are_safe_retryable_and_do_not_auto_retry(
    api, monkeypatch, failure,
):
    """运行实际 Provider 解析，但 HTTP send 为测试替身；不连接外网或真实模型。"""
    calls = []

    async def send(self, request, **kwargs):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(SAFE_ERROR, request=request)
        content = (SAFE_ERROR if failure == "json" else
                   json.dumps({"title": None} if failure == "schema" else {"title": "合成活动"}))
        body = {} if failure == "envelope" else {"choices": [{"message": {"content": content}}]}
        return httpx.Response(
            int(failure) if failure in {"429", "500"} else 200, json=body, request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    provider = OpenAIProvider(api_key="synthetic-key", base_url="https://model.invalid/v1")
    monkeypatch.setattr(ExtractionService, "_get_llm_provider", lambda self: provider)
    source_id = source(api)
    result = api.post(f"/api/source-items/{source_id}/extract")
    assert result.status_code == 502 and SAFE_ERROR not in result.text
    assert current(api, source_id)["parse_status"] == "failed"
    assert SAFE_ERROR not in current(api, source_id)["parse_error"]
    assert len(calls) == 1 and counts() == (0, 0, 0)
    monkeypatch.setattr(ExtractionService, "_get_llm_provider", lambda self: api.provider)
    assert api.post(f"/api/source-items/{source_id}/extract").status_code == 200
    assert counts()[0] == 1


def test_cancelled_model_call_leaves_retryable_state(api):
    source_id = source(api)
    api.provider.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ExtractionService().extract(source_id))
    assert current(api, source_id)["parse_status"] == "failed"
    assert counts() == (0, 0, 0)
    api.provider.error = None
    assert api.post(f"/api/source-items/{source_id}/extract").status_code == 200


# ---------- T-004 收尾：Confirm 幂等重试 / PATCH 迁移矩阵 / 陈旧 pending 接管 ----------

def _first_draft(api):
    items = api.get("/api/review-queue").json()
    assert items, "缺少待核对草稿"
    return items[0]["extraction"]


def _force_pending(source_id, minutes_ago):
    """模拟进程崩溃遗留的 pending：直接改解析状态与认领时间戳。"""
    stale = (now_shanghai() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute(
            "UPDATE source_items SET parse_status = 'pending', updated_at = ? WHERE id = ?",
            (stale, source_id),
        )
        connection.commit()


def test_confirm_retry_returns_same_event_without_duplicates(api):
    """确认响应丢失后的原样重试：返回同一活动，不重复建档或新增证据。"""
    enter(api, "manual")
    draft = _first_draft(api)
    payload = {"status": "interested", "expected_revision": draft["revision"]}
    first = api.post(f"/api/review-queue/{draft['id']}/confirm", json=payload)
    assert first.status_code == 200, first.text
    after_first = counts()
    second = api.post(f"/api/review-queue/{draft['id']}/confirm", json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert counts() == after_first


def test_confirm_retry_with_different_payload_returns_original_event(api):
    """已确认草稿的重试携带不同 status/extracted：只读返回原活动，不二次修改。"""
    enter(api, "manual")
    draft = _first_draft(api)
    first = api.post(f"/api/review-queue/{draft['id']}/confirm", json={"status": "interested"})
    assert first.status_code == 200, first.text
    second = api.post(f"/api/review-queue/{draft['id']}/confirm", json={
        "status": "applied", "extracted": {"title": "确认时改标题"},
    })
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["id"] == first.json()["id"]
    assert body["status"] == "interested"
    assert body["title"] == "合成活动"


def test_confirm_after_reject_is_clear_conflict(api):
    """已放弃草稿的确认：返回明确的 409 与文案，不建档。"""
    enter(api, "manual")
    draft = _first_draft(api)
    assert api.post(f"/api/review-queue/{draft['id']}/reject").status_code == 204
    result = api.post(f"/api/review-queue/{draft['id']}/confirm", json={"status": "interested"})
    assert result.status_code == 409
    assert "放弃" in result.json()["detail"]
    assert counts()[2] == 0


def test_legacy_confirmed_without_event_anchor_is_conflict(api):
    """迁移前已确认且无锚点可查的老数据：409 指引活动库，不猜测返回。"""
    enter(api, "manual")
    draft = _first_draft(api)
    confirmed = api.post(f"/api/review-queue/{draft['id']}/confirm", json={"status": "interested"})
    assert confirmed.status_code == 200, confirmed.text
    with connect() as connection:
        connection.execute("DELETE FROM evidences WHERE extraction_result_id = ?", (draft["id"],))
        connection.execute(
            "UPDATE extraction_results SET confirmed_event_id = NULL WHERE id = ?",
            (draft["id"],),
        )
        connection.commit()
    result = api.post(f"/api/review-queue/{draft['id']}/confirm", json={"status": "interested"})
    assert result.status_code == 409
    assert "活动库" in result.json()["detail"]


def test_concurrent_confirm_creates_single_event(api):
    """两个并发确认同一草稿：恰好建档一次，双方拿到同一活动。"""
    enter(api, "manual")
    draft = _first_draft(api)
    payload = {"status": "interested", "expected_revision": draft["revision"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: api.post(f"/api/review-queue/{draft['id']}/confirm", json=payload),
            range(2),
        ))
    assert [result.status_code for result in results] == [200, 200]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert counts()[2] == 1


def test_event_patch_status_respects_transition_matrix(api):
    """字段修正入口与状态端点共用迁移矩阵：非法迁移两处一致拒绝。"""
    enter(api, "manual")
    draft = _first_draft(api)
    confirmed = api.post(
        f"/api/review-queue/{draft['id']}/confirm", json={"status": "needs_review"}
    )
    assert confirmed.status_code == 200, confirmed.text
    event_id = confirmed.json()["id"]

    via_patch = api.patch(f"/api/events/{event_id}", json={"status": "done"})
    assert via_patch.status_code == 400, via_patch.text
    assert "不能从 needs_review" in via_patch.json()["detail"]
    via_status = api.patch(f"/api/events/{event_id}/status", json={"status": "done"})
    assert via_status.status_code == 400

    legal = api.patch(
        f"/api/events/{event_id}", json={"status": "interested", "location": "上海"}
    )
    assert legal.status_code == 200, legal.text
    assert legal.json()["status"] == "interested"
    assert legal.json()["location"] == "上海"

    idempotent = api.patch(
        f"/api/events/{event_id}", json={"status": "interested", "organizer": "新主办方"}
    )
    assert idempotent.status_code == 200, idempotent.text
    assert idempotent.json()["organizer"] == "新主办方"


def test_stale_pending_reclaimed_after_timeout(api):
    """崩溃遗留 pending：宽限期内仍拒绝，超过阈值后可重新认领并完成抽取。"""
    source_id = source(api)
    _force_pending(source_id, minutes_ago=0)
    busy = api.post(f"/api/source-items/{source_id}/extract")
    assert busy.status_code == 409
    assert busy.json()["detail"] == "来源正在抽取，请稍后重试；长时间无结果可稍后再试或核对状态"

    _force_pending(source_id, minutes_ago=20)
    reclaimed = api.post(f"/api/source-items/{source_id}/extract")
    assert reclaimed.status_code == 200, reclaimed.text
    assert current(api, source_id)["parse_status"] == "parsed"
    assert _first_draft(api)["extracted"]["title"] == "合成活动"


NO_EVENT_BODY = ("今天天气不错，我去公园散步，顺便买了一杯咖啡，坐在长椅上看了一会儿书。"
                  "下午回家整理了房间，给花浇了水，晚上读了几十页小说，平淡又放松的一天。")


def test_no_event_text_returns_no_event_without_draft(api):
    """无活动文本：来源标 no_event、不建草稿与证据，返回可读提示。"""
    source_id = source(api, raw_text=NO_EVENT_BODY)
    api.provider.empty_title = True
    result = api.post(f"/api/source-items/{source_id}/extract")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "no_event"
    assert "未发现活动" in body["message"]
    assert current(api, source_id)["parse_status"] == "no_event"
    assert counts()[:2] == (0, 0)


def test_no_event_retry_is_idempotent_without_model_call(api):
    """no_event 的重复抽取幂等返回提示，不再调用模型或建草稿。"""
    api.provider.empty_title = True
    source_id = source(api)
    assert api.post(f"/api/source-items/{source_id}/extract").status_code == 200
    calls_after_first = api.provider.calls
    again = api.post(f"/api/source-items/{source_id}/extract")
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "no_event"
    assert api.provider.calls == calls_after_first
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM extraction_results").fetchone()[0] == 0


def test_completion_no_event_reports_clear_state(api):
    """分享补全后未发现活动：ready + parse_status=no_event + 无草稿 id。"""
    api.provider.empty_title = True
    source_id = api.post("/api/share/intake", json={}).json()["source_item_id"]
    result = api.post(
        f"/api/share/{source_id}/complete",
        json={"raw_text": NO_EVENT_BODY, "auto_extract": True},
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["state"] == "ready"
    assert data["parse_status"] == "no_event"
    assert data["extraction_id"] is None
    assert data["extraction_started"] is True

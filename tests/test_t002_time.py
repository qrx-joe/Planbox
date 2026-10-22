"""T-002 定向回归，显式收集本文件；临时 SQLite 与网络调用隔离。"""

import asyncio
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import httpx
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.repositories.extraction_repository import ExtractionRepository, dumps_json
from backend.app.schemas.event import EventRead, EventUpdate
from backend.app.schemas.extraction import ExtractedEventDraft
from backend.app.services.calendar_service import CalendarService
from backend.app.services.extraction_service import ExtractionService

TIME_FIELDS = [
    "registration_start_at",
    "registration_deadline_at",
    "event_start_at",
    "event_end_at",
]
BAD_TIMES = [
    "2026-02-30",
    "2026-10-20T25:00:00+08:00",
    "2026-10-20T09:00:00",
    "2026-10-20T09:00:00-00:00",
    "1月5日",
    "明天18:00",
    "20261020",
    "",
    "2026-10-20T09:00:00+08:99",
    "2026-10-20T09:00:00+24:00",
]


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("T-002 禁止外部请求")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


@pytest.fixture
def client(monkeypatch):
    from pathlib import Path

    from backend.app.api import calendar, events, review, source_items
    from backend.app.db.database import initialize_database

    root = PROJECT_ROOT / ".testdata"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t002-", dir=root) as directory:
        path = Path(directory)
        monkeypatch.setattr(settings, "database_path", path / "time.db")
        monkeypatch.setattr(settings, "data_dir", path)
        monkeypatch.setattr(settings, "upload_dir", path / "uploads")
        monkeypatch.setattr(settings, "llm_provider", "fake")
        initialize_database()
        app = FastAPI()
        app.include_router(source_items.router, prefix="/api/source-items")
        app.include_router(review.router, prefix="/api/review-queue")
        app.include_router(events.router, prefix="/api/events")
        app.include_router(calendar.router, prefix="/api/calendar")
        with TestClient(app, raise_server_exceptions=False) as api:
            yield api


def make_event(**values):
    return EventRead(
        id="synthetic",
        title="合成活动",
        status="interested",
        missing_fields=[],
        created_at="2026-10-18T00:00:00+08:00",
        updated_at="2026-10-18T00:00:00+08:00",
        **values,
    )


def seed_draft(client, **values):
    source = client.post("/api/source-items", json={"raw_text": "合成活动正文，无时间。"}).json()
    timestamp = "2026-10-18T00:00:00+08:00"
    draft = {"title": "合成活动", **values}
    return ExtractionRepository().create(
        {
            "id": "ext-" + source["id"],
            "source_item_id": source["id"],
            "status": "pending_review",
            "extracted_json": dumps_json(draft),
            "missing_fields": "[]",
            "confidence": 0.5,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )["id"]


def seed_event(client, **values):
    draft_id = seed_draft(client, **values)
    result = client.post(f"/api/review-queue/{draft_id}/confirm", json={})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("field", TIME_FIELDS)
@pytest.mark.parametrize("value", BAD_TIMES)
def test_new_updates_reject_invalid_or_unresolved_time(field, value):
    with pytest.raises(ValidationError):
        EventUpdate(**{field: value})


@pytest.mark.parametrize(
    "value", ["2026-02-28", "2024-02-29", "2026-10-20T09:00:00+08:00", "2026-10-20T01:00:00Z"]
)
def test_date_and_aware_time_keep_their_precision(value):
    assert EventUpdate(event_start_at=value).event_start_at == value


@pytest.mark.parametrize(
    "values",
    [
        {"event_start_at": "2026-10-21", "event_end_at": "2026-10-20"},
        {"registration_start_at": "2026-10-21", "registration_deadline_at": "2026-10-20"},
        {"event_start_at": "2026-10-20", "event_end_at": "2026-10-21T09:00:00+08:00"},
        {"schedule": [{"name": "提交", "start_at": "2026-10-21", "end_at": "2026-10-20"}]},
        {"schedule": [{"name": "提交", "start_at": "2026-02-30"}]},
    ],
)
def test_ranges_and_schedule_are_validated(values):
    with pytest.raises(ValidationError):
        EventUpdate(**values)


@pytest.mark.parametrize("value", BAD_TIMES[:6])
def test_drafts_preserve_uncertain_values_for_review(value):
    draft = ExtractedEventDraft(title="待核对", registration_deadline_at=value)
    assert draft.registration_deadline_at == value
    assert draft.time_issues[0].field == "registration_deadline_at"
    assert draft.time_issues[0].raw_value == value


def test_deadline_rule_does_not_invent_year_or_clock():
    service = ExtractionService()
    assert service._guess_deadline("报名截止2027年1月5日")[0] == "2027-01-05"
    assert service._guess_deadline("报名截止1月5日")[0] == "1月5日"
    assert service._guess_deadline("报名截止2026-10-20 18:00")[0] == "2026-10-20T18:00:00"
    assert (
        service._guess_deadline("报名截止2026-10-20T18:00:00+08:00")[0]
        == "2026-10-20T18:00:00+08:00"
    )
    assert service._guess_deadline("举办时间2027年1月5日")[0] is None
    assert service._guess_deadline("报名时间2027年1月1日至1月5日")[0] is None


@pytest.mark.parametrize(
    "published, reference_kind",
    [("2026-12-20T12:00:00+08:00", "published_at"), (None, "captured_at")],
)
def test_incomplete_date_retains_actual_source_reference(client, published, reference_kind):
    from backend.app.schemas.source_item import SourceItemCreate
    from backend.app.services.source_service import SourceService

    source = SourceService().create(
        SourceItemCreate(raw_text="报名截止1月5日", published_at=published)
    )
    draft = asyncio.run(ExtractionService().extract(source.id)).extracted
    issue = draft.time_issues[0]
    assert issue.raw_value == "1月5日"
    assert issue.reference_kind == reference_kind
    assert issue.reference_at == (published or source.captured_at)


def test_calendar_date_only_deadline_is_all_day_without_invented_clock():
    content = CalendarService().export_events([make_event(registration_deadline_at="2026-10-20")])
    assert "DTSTART;VALUE=DATE:20261020\r\n" in content
    assert "T235900" not in content and "T000000" not in content


def test_calendar_date_range_includes_source_end_day():
    content = CalendarService().export_events(
        [make_event(event_start_at="2026-10-20", event_end_at="2026-10-22")]
    )
    assert "DTSTART;VALUE=DATE:20261020\r\n" in content
    assert "DTEND;VALUE=DATE:20261023\r\n" in content


def test_calendar_precise_time_has_no_fabricated_duration():
    content = CalendarService().export_events(
        [make_event(event_start_at="2026-10-20T09:00:00+08:00")]
    )
    assert "DTSTART:20261020T010000Z\r\n" in content
    assert "DTEND" not in content


def test_calendar_known_end_without_start_is_not_lost():
    content = CalendarService().export_events(
        [make_event(event_end_at="2026-10-20T09:00:00+08:00")]
    )
    assert content.count("BEGIN:VEVENT") == 1
    assert "活动结束" in content and "DTSTART:20261020T010000Z" in content


def test_calendar_utf8_folding_and_text_cannot_inject_lines():
    event = make_event(event_start_at="2026-10-20")
    event.title = "很长的中文活动标题" * 18 + "\r\nBEGIN:VALARM"
    content = CalendarService().export_events([event])
    lines = content.split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    assert all("\r" not in line and "\n" not in line for line in lines)
    assert "BEGIN:VALARM" not in lines
    content.encode("utf-8").decode("utf-8")


@pytest.mark.parametrize("value", BAD_TIMES[:6])
def test_confirm_rejects_old_invalid_draft_before_writing(client, value):
    draft_id = seed_draft(client, event_start_at=value)
    result = client.post(f"/api/review-queue/{draft_id}/confirm", json={})
    assert result.status_code == 400
    assert client.get("/api/events?paginated=false").json() == []


def test_patch_checks_range_against_stored_half_and_does_not_change_status(client):
    event = seed_event(client, event_start_at="2026-10-20", event_end_at="2026-10-21")
    result = client.patch(
        f"/api/events/{event['id']}", json={"event_end_at": "2026-10-19", "status": "done"}
    )
    assert result.status_code in (400, 422)
    current = client.get(f"/api/events/{event['id']}").json()
    assert current["event_end_at"] == "2026-10-21" and current["status"] == "interested"


def test_bad_legacy_event_is_readable_but_export_has_review_list(client):
    from backend.app.api import calendar

    event = make_event(event_start_at="2026-02-30")
    original = calendar.event_service.list_actionable
    calendar.event_service.list_actionable = Mock(return_value=[event])
    try:
        result = client.get("/api/calendar/export.ics")
    finally:
        calendar.event_service.list_actionable = original
    assert result.status_code == 409
    detail = result.json()["detail"]
    assert detail["code"] == "CALENDAR_TIME_REVIEW_REQUIRED"
    assert detail["issues"][0]["event_id"] == event.id
    assert detail["issues"][0]["field"] == "event_start_at"


def test_query_uses_actual_instant_for_different_offsets_and_pagination(client):
    early = seed_event(client, title="早", registration_deadline_at="2026-10-20T09:00:00+08:00")
    late = seed_event(client, title="晚", registration_deadline_at="2026-10-20T02:00:00Z")
    result = client.get(
        "/api/events", params={"deadline_before": "2026-10-20T01:30:00Z", "limit": 1}
    ).json()
    assert result["total"] == 1 and result["items"][0]["id"] == early["id"]
    all_events = client.get("/api/events", params={"sort": "deadline"}).json()
    assert [event["id"] for event in all_events["items"]] == [early["id"], late["id"]]


def test_date_query_includes_calendar_day_without_assuming_deadline_hour(client):
    event = seed_event(client, registration_deadline_at="2026-10-20")
    result = client.get("/api/events", params={"deadline_after": "2026-10-20T12:00:00Z"}).json()
    assert result["total"] == 1 and result["items"][0]["id"] == event["id"]


@pytest.mark.parametrize(
    "params",
    [
        {"deadline_after": "2026-02-30"},
        {"event_start_before": "2026-10-20T09:00:00"},
        {"deadline_after": "2026-10-21", "deadline_before": "2026-10-20"},
    ],
)
def test_bad_query_time_is_a_readable_client_error(client, params):
    result = client.get("/api/events", params=params)
    assert result.status_code in (400, 422)
    assert result.json().get("detail")


def test_legacy_values_remain_readable_and_are_excluded_from_precise_query(client):
    from backend.app.repositories.event_repository import EventRepository

    early = seed_event(client, registration_deadline_at="2026-10-20T01:00:00Z")
    legacy = seed_event(client, registration_deadline_at="2026-10-20T02:00:00Z")
    EventRepository().update(legacy["id"], {"registration_deadline_at": "2026-02-30"})
    result = client.get("/api/events").json()
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [early["id"], legacy["id"]]
    assert result["items"][1]["time_issues"][0]["code"] == "invalid"
    filtered = client.get("/api/events", params={"deadline_before": "2027-01-01"}).json()
    assert filtered["total"] == 1
    assert (
        client.patch(f"/api/events/{legacy['id']}", json={"notes": "先保留原值"}).status_code == 200
    )


def test_date_query_uses_configured_calendar_day_for_precise_records(client):
    within = seed_event(client, event_start_at="2026-10-20T15:59:00Z")
    seed_event(client, event_start_at="2026-10-20T16:01:00Z")
    result = client.get("/api/events", params={"event_start_before": "2026-10-20"}).json()
    assert result["total"] == 1 and result["items"][0]["id"] == within["id"]


def test_merge_cannot_create_inverted_range_or_transfer_sources(client):
    target = seed_event(client, event_start_at="2026-10-20")
    source = seed_event(client, event_end_at="2026-10-19")
    result = client.post(
        "/api/events/merge", json={"target_id": target["id"], "source_id": source["id"]}
    )
    assert result.status_code == 400
    assert client.get("/api/events").json()["total"] == 2
    assert client.get(f"/api/events/{target['id']}").json()["event_end_at"] is None


def test_model_draft_uses_same_time_review_boundary(client):
    from backend.app.providers.llm import BaseLLMProvider
    from backend.app.schemas.source_item import SourceItemCreate
    from backend.app.services.source_service import SourceService

    class StubProvider(BaseLLMProvider):
        async def extract_event(self, text, url=None):
            return ExtractedEventDraft(
                title="模型替身", event_start_at="明天18:00", registration_deadline_at="2027-01-05"
            )

    source = SourceService().create(
        SourceItemCreate(raw_text="明天18:00活动，报名截止2027年1月5日")
    )
    draft = asyncio.run(ExtractionService(llm_provider=StubProvider()).extract(source.id)).extracted
    assert draft.registration_deadline_at == "2027-01-05"
    assert draft.event_start_at == "明天18:00"
    assert draft.time_issues[0].reference_at == source.captured_at


def test_export_unknown_time_is_explicit_not_an_empty_calendar(client):
    event = seed_event(client)
    result = client.get(f"/api/calendar/events/{event['id']}.ics")
    assert result.status_code == 409
    assert result.json()["detail"]["issues"][0]["event_id"] == event["id"]

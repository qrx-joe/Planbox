"""T-003 的证据定位阶段；人工更正历史在后续子步骤验收。"""

import pytest

from backend.app.core.evidence import locate_evidence
from backend.app.schemas.extraction import ExtractedEventDraft
from backend.app.services.extraction_service import ExtractionService


@pytest.fixture
def evidence_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import evidences
    from backend.app.core.config import settings
    from backend.app.db.database import SCHEMA, connect

    monkeypatch.setattr(settings, "database_path", tmp_path / "evidence.db")
    with connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO source_items "
            "(id, source_type, raw_text, captured_at, parse_status, created_at, updated_at) "
            "VALUES ('src', 'text', ?, 'now', 'parsed', 'now', 'now')",
            ("背景。" * 150 + "主办方甲，报名截止2027年1月5日",),
        )
        connection.execute(
            "INSERT INTO events (id,title,organizer,status,created_at,updated_at) "
            "VALUES ('event','活动','主办方乙','interested','now','now')"
        )
        for identifier, field, value, quote in [
            ("old", "organizer", "主办方甲", "错误的历史片段"),
            ("new", "organizer", "主办方乙", "背景。" * 20),
            ("date", "registration_deadline_at", "2027-01-05", None),
            ("unknown", "invented_field", "虚构", "背景。"),
        ]:
            connection.execute(
                "INSERT INTO evidences "
                "(id,event_id,source_item_id,field_name,field_value,evidence_text,created_at) "
                "VALUES (?, 'event', 'src', ?, ?, ?, 'now')",
                (identifier, field, value, quote),
            )
        connection.commit()
    app = FastAPI()
    app.include_router(evidences.router, prefix="/api/evidences")
    with TestClient(app) as client:
        yield client


def test_read_api_rechecks_legacy_quotes_and_returns_real_offsets(evidence_api):
    items = {item["id"]: item for item in evidence_api.get("/api/evidences/event").json()["items"]}
    old = items["old"]
    assert old["verification_status"] == "located"
    assert old["start_offset"] > 200
    assert "主办方甲" in old["evidence_text"]
    assert old["value_relation"] == "differs_from_current"
    assert items["new"]["value_relation"] == "matches_current"
    assert items["new"]["verification_status"] == "unverified"
    assert items["new"]["evidence_text"] is None
    assert items["date"]["value_relation"] == "no_current_value"
    assert items["date"]["verification_status"] == "located"
    assert items["unknown"]["value_relation"] == "unknown_field"
    assert all("source_text" not in item for item in items.values())


def test_read_api_is_read_only_and_does_not_turn_legacy_quote_into_fact(evidence_api):
    from backend.app.db.database import connect

    evidence_api.get("/api/evidences/event")
    with connect() as connection:
        row = connection.execute("SELECT evidence_text FROM evidences WHERE id='old'").fetchone()
        assert row["evidence_text"] == "错误的历史片段"


@pytest.mark.parametrize("field,value,current,extra,expected", [
    ("is_online", "False", 0, {}, "matches_current"),
    ("event_start_at", "2027-01-05T01:00:00Z", "2027-01-05T09:00:00+08:00", {},
     "matches_current"),
    ("schedule.0.name", "初赛", None,
     {"current_schedule_json": '[{"name":"初赛"}]'}, "matches_current"),
    ("schedule.-1.name", "初赛", None,
     {"current_schedule_json": '[{"name":"初赛"}]'}, "unknown_field"),
    ("schedule.0.name", "初赛", None,
     {"current_schedule_json": 'broken'}, "unknown_field"),
])
def test_read_projection_handles_known_types_and_bad_legacy_schedule(
    field, value, current, extra, expected,
):
    from backend.app.services.evidence_service import evidence_to_read

    row = {"id": "e", "event_id": "event", "current_event_id": "event",
           "source_item_id": "src", "field_name": field, "field_value": value,
           "created_at": "now", "current_" + field: current, **extra}
    result = evidence_to_read(row)
    assert result.value_relation == expected
    assert result.verification_status == "unverified"


def test_draft_read_does_not_claim_value_is_current(evidence_api):
    from backend.app.db.database import connect

    with connect() as connection:
        # 外键启用后证据必须挂在真实存在的草稿上；先补父行再改挂
        connection.execute(
            "INSERT INTO extraction_results "
            "(id,source_item_id,status,extracted_json,created_at,updated_at) "
            "VALUES ('ext','src','pending_review','','now','now')"
        )
        connection.execute("UPDATE evidences SET event_id=NULL, extraction_result_id='ext'")
        connection.commit()
    result = evidence_api.get("/api/evidences", params={"extraction_result_id": "ext"})
    assert result.status_code == 200
    assert all(item["value_relation"] == "draft" for item in result.json()["items"])


def test_short_unrelated_text_is_not_evidence():
    assert locate_evidence("不存在的主办方", "这是另一条活动信息") is None


def test_long_text_does_not_fallback_to_first_200_characters():
    assert locate_evidence("虚构字段", "无关内容。" * 100) is None


def test_late_date_evidence_points_to_actual_source_slice():
    text = "活动背景。" * 100 + "报名截止2027年1月5日，请及时报名。"
    draft = ExtractedEventDraft(title="未提供标题", registration_deadline_at="2027-01-05")
    evidence = next(
        item
        for item in ExtractionService()._generate_evidences(draft, text)
        if item.field == "registration_deadline_at"
    )
    assert evidence.verification_status == "located"
    assert evidence.start_offset > 200
    assert text[evidence.start_offset : evidence.end_offset] == evidence.evidence
    assert "2027年1月5日" in evidence.evidence
    assert evidence.confidence is None


def test_unlocated_field_is_explicit_and_has_no_manufactured_confidence():
    draft = ExtractedEventDraft(title="示例活动", organizer="幻觉机构")
    evidence = next(
        item
        for item in ExtractionService()._generate_evidences(draft, "示例活动，欢迎参加")
        if item.field == "organizer"
    )
    assert evidence.evidence is None
    assert evidence.verification_status == "unverified"
    assert evidence.start_offset is None and evidence.end_offset is None
    assert evidence.confidence is None


def test_date_fragment_does_not_prove_an_invented_timezone_or_hour():
    draft = ExtractedEventDraft(title="示例", event_start_at="2027-01-05T09:00:00+08:00")
    evidence = next(
        item
        for item in ExtractionService()._generate_evidences(draft, "活动举办日期2027年1月5日")
        if item.field == "event_start_at"
    )
    assert evidence.verification_status == "unverified" and evidence.evidence is None


def test_all_present_key_fields_have_either_a_location_or_unverified_status():
    draft = ExtractedEventDraft(
        title="活动",
        registration_start_at="2026-10-01",
        registration_deadline_at="2026-10-20",
        event_start_at="2026-11-01",
        event_end_at="2026-11-03",
        prize="奖金",
        team_required="需组队",
        audience="学生",
        schedule=[{"name": "初赛", "start_at": "2026-11-01"}],
    )
    result = ExtractionService()._generate_evidences(draft, "活动面向学生，需组队，有奖金")
    fields = {item.field for item in result}
    assert {
        "registration_start_at",
        "event_end_at",
        "prize",
        "team_required",
        "audience",
        "schedule.0.start_at",
    } <= fields
    assert all(item.verification_status in {"located", "unverified"} for item in result)


def test_fake_source_url_is_a_candidate_not_a_quote():
    draft = ExtractionService()._rule_based_extract(
        {"raw_text": "示例活动", "source_url": "https://example.com/article"}
    )
    evidence = next(item for item in draft.evidences if item.field == "registration_url")
    assert evidence.evidence is None and evidence.verification_status == "unverified"
    assert evidence.confidence is None

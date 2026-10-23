"""Review 保存/更正/确认的隔离回归；显式收集，不使用真实来源或模型。"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.db.database import connect, initialize_database


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.app.api import events, evidences, review

    monkeypatch.setattr(settings, "database_path", tmp_path / "review.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    initialize_database()
    with connect() as connection:
        for suffix in ("a", "b"):
            connection.execute(
                "INSERT INTO source_items "
                "(id,source_type,raw_text,captured_at,parse_status,created_at,updated_at) "
                "VALUES (?,'text',?,'2026-10-23T09:00:00+08:00','parsed','now','now')",
                (f"src-{suffix}", "合成原文。" * 100 + "甲机构，报名截止2027年1月5日，备选1月6日"),
            )
            connection.execute(
                "INSERT INTO extraction_results "
                "(id,source_item_id,status,extracted_json,missing_fields,confidence,"
                "created_at,updated_at) "
                "VALUES (?,?,'pending_review',?,'[]',0.5,'now','now')",
                (suffix, f"src-{suffix}", json.dumps({
                    "title": f"合成草稿{suffix}", "organizer": "甲机构",
                    "registration_deadline_at": "2027-01-05", "missing_fields": [],
                }, ensure_ascii=False)),
            )
        connection.commit()
    app = FastAPI()
    app.include_router(review.router, prefix="/api/review-queue")
    app.include_router(events.router, prefix="/api/events")
    app.include_router(evidences.router, prefix="/api/evidences")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def history(api, identifier="a"):
    response = api.get(f"/api/review-queue/{identifier}/corrections")
    assert response.status_code == 200, response.text
    return response.json()


def edit(api, changes, revision=None, **extra):
    if revision is None:
        revision = history(api)["revision"]
    return api.patch("/api/review-queue/a", json={
        "changes": changes, "expected_revision": revision, **extra,
    })


def current(api):
    response = api.get("/api/review-queue/a")
    assert response.status_code == 200, response.text
    return response.json()


def test_review_can_be_saved_reopened_and_missing_fields_recomputed(api):
    response = edit(api, {"registration_deadline_at": "2027-01-06"}, reason="核对日期")
    assert response.status_code == 200, response.text
    result = current(api)
    assert result["extracted"]["registration_deadline_at"] == "2027-01-06"
    assert "registration_deadline_at" not in result["missing_fields"]
    assert result["revision"] == 1
    item = history(api)["items"][0]
    assert item["old_value"] == "2027-01-05" and item["new_value"] == "2027-01-06"
    assert item["source_item_id"] == "src-a" and item["reason"] == "核对日期"
    assert item["actor"] == "local_user_unverified"


def test_unresolved_time_can_be_retained_in_draft_but_cannot_be_confirmed(api):
    assert edit(api, {"registration_deadline_at": "1月5日"}).status_code == 200
    draft = current(api)["extracted"]
    assert draft["time_issues"][0]["code"] == "incomplete"
    assert "registration_deadline_at" in draft["missing_fields"]
    assert api.post("/api/review-queue/a/confirm", json={}).status_code == 400
    assert api.get("/api/events?paginated=false").json() == []


@pytest.mark.parametrize("field", ["status", "confidence", "evidences", "time_issues", "notes"])
def test_review_changes_cannot_forge_metadata_or_lifecycle(api, field):
    response = api.patch("/api/review-queue/a", json={
        "changes": {field: "forged"}, "expected_revision": 0,
    })
    assert response.status_code == 422


def test_review_types_unknown_and_stale_version_are_preserved(api):
    assert edit(api, {"organizer": None, "is_online": False}).status_code == 200
    draft = current(api)["extracted"]
    assert draft["organizer"] is None and draft["is_online"] is False
    assert edit(api, {"organizer": "不能覆盖"}, revision=0).status_code == 409
    assert history(api, "b")["items"] == []


def test_draft_retraction_keeps_history_and_allows_unresolved_old_values(api):
    edit(api, {"registration_deadline_at": "1月5日"})
    edit(api, {"registration_deadline_at": "2027-01-06"})
    original = history(api)["items"][0]
    url = f"/api/review-queue/a/corrections/{original['operation_id']}/retract"
    assert api.post(url, json={"reason": "误改"}).status_code == 200
    assert current(api)["extracted"]["registration_deadline_at"] == "1月5日"
    assert len(history(api)["items"]) == 3
    assert api.post(url, json={"reason": "重试"}).status_code == 200
    assert len(history(api)["items"]) == 3


def test_later_draft_change_blocks_old_retraction(api):
    edit(api, {"organizer": "乙机构"})
    operation = history(api)["items"][0]["operation_id"]
    edit(api, {"organizer": "丙机构"})
    edit(api, {"organizer": "乙机构"})
    assert api.post(f"/api/review-queue/a/corrections/{operation}/retract",
                    json={"reason": "撤回"}).status_code == 409


def test_draft_evidence_keeps_old_candidate_and_marks_current_relation(api):
    assert edit(api, {"organizer": "未证实机构"}).status_code == 200
    items = api.get("/api/evidences?extraction_result_id=a").json()["items"]
    old = next(item for item in items if item["field_value"] == "甲机构")
    new = next(item for item in items if item["field_value"] == "未证实机构")
    assert old["value_relation"] == "differs_from_draft" and old["verification_status"] == "located"
    assert new["value_relation"] == "matches_draft" and new["verification_status"] == "unverified"
    assert new["confidence"] is None and new["evidence_text"] is None


def test_evidence_failure_rolls_back_draft_and_history(api):
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_evidence BEFORE INSERT ON evidences "
            "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        connection.commit()
    response = api.patch("/api/review-queue/a", json={
        "changes": {"organizer": "乙机构"}, "expected_revision": 0,
    })
    assert response.status_code == 500
    assert current(api)["extracted"]["organizer"] == "甲机构"
    assert history(api)["total"] == 0


def test_confirm_uses_saved_revision_and_freezes_review(api):
    edit(api, {"organizer": "乙机构"})
    assert api.post("/api/review-queue/a/confirm", json={"expected_revision": 0}).status_code == 409
    result = api.post("/api/review-queue/a/confirm", json={"expected_revision": 1})
    assert result.status_code == 200, result.text
    assert result.json()["organizer"] == "乙机构"
    assert edit(api, {"organizer": "不允许"}).status_code == 409
    assert not history(api)["items"][0]["can_retract"]
    assert api.post("/api/review-queue/a/confirm", json={}).status_code == 400


def test_legacy_confirm_override_is_audited_not_trusted_as_evidence(api):
    response = api.post("/api/review-queue/a/confirm", json={"extracted": {
        "title": "人工改标题", "organizer": "幻觉", "evidences": [
            {"field": "organizer", "value": "幻觉", "evidence": "伪造片段", "confidence": 1},
        ],
    }})
    assert response.status_code == 200, response.text
    assert any(item["field_name"] == "title" for item in history(api)["items"])
    evidences = api.get(f"/api/evidences/{response.json()['id']}").json()["items"]
    assert all(item["evidence_text"] != "伪造片段" for item in evidences)


def test_failed_confirmation_rolls_back_event_sources_and_draft_changes(api):
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_confirm BEFORE UPDATE ON extraction_results "
            "WHEN NEW.status='confirmed' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        connection.commit()
    response = api.post("/api/review-queue/a/confirm", json={"extracted": {"title": "临时标题"}})
    assert response.status_code == 500
    assert current(api)["extracted"]["title"] == "合成草稿a"
    assert history(api)["total"] == 0
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0] == 0


def test_concurrent_confirm_and_edit_never_confirm_a_stale_draft(api):
    barrier = Barrier(2)

    def run(action):
        barrier.wait(timeout=5)
        if action == "edit":
            return api.patch("/api/review-queue/a", json={
                "changes": {"organizer": "乙机构"}, "expected_revision": 0,
            }).status_code
        return api.post("/api/review-queue/a/confirm", json={"expected_revision": 0}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(run, ["edit", "confirm"]))
    assert sorted(values) == [200, 409]
    events = api.get("/api/events?paginated=false").json()
    if values[0] == 200:
        assert events == []
        assert current(api)["extracted"]["organizer"] == "乙机构"
    else:
        assert len(events) == 1 and events[0]["organizer"] == "甲机构"


def test_missing_review_and_cross_entity_retraction_return_404(api):
    assert api.get("/api/review-queue/missing").status_code == 404
    edit(api, {"organizer": "乙机构"})
    operation = history(api)["items"][0]["operation_id"]
    assert api.post(f"/api/review-queue/b/corrections/{operation}/retract",
                    json={"reason": "撤回"}).status_code == 404


def test_rejected_draft_is_readable_but_cannot_be_edited(api):
    assert api.post("/api/review-queue/a/reject").status_code == 204
    assert current(api)["status"] == "rejected"
    assert edit(api, {"organizer": "不允许"}, revision=0).status_code == 409


def test_invalid_legacy_confirm_override_rolls_back_the_review_edit(api):
    response = api.post("/api/review-queue/a/confirm", json={"extracted": {
        "title": "尚不能确认", "registration_deadline_at": "1月5日",
    }})
    assert response.status_code == 400
    assert current(api)["extracted"]["title"] == "合成草稿a"
    assert history(api)["total"] == 0


def test_custom_unknowns_are_preserved_and_noop_does_not_create_history(api):
    with connect() as connection:
        row = connection.execute(
            "SELECT extracted_json FROM extraction_results WHERE id='a'",
        ).fetchone()
        data = json.loads(row[0])
        data["missing_fields"] = ["eligibility_detail"]
        connection.execute("UPDATE extraction_results SET extracted_json=? WHERE id='a'",
                           (json.dumps(data),))
        connection.commit()
    assert edit(api, {"organizer": "甲机构"}).status_code == 200
    assert history(api)["total"] == 0
    assert edit(api, {"organizer": "乙机构"}).status_code == 200
    assert "eligibility_detail" in current(api)["missing_fields"]


def test_draft_migration_failure_keeps_prior_version_and_can_retry(api):
    import sqlite3

    with connect() as connection:
        connection.execute("DROP TABLE draft_corrections")
        connection.execute("DELETE FROM schema_migrations WHERE version='0002_draft_corrections'")
        connection.execute(
            "CREATE TRIGGER reject_draft_migration BEFORE INSERT ON schema_migrations "
            "WHEN NEW.version='0002_draft_corrections' "
            "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        before = [tuple(row) for row in connection.execute("SELECT * FROM extraction_results")]
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        initialize_database()
    with connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='draft_corrections'",
        ).fetchone() is None
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row[0] for row in versions] == ["0001_event_corrections"]
        after = [tuple(row) for row in connection.execute("SELECT * FROM extraction_results")]
        assert after == before
        connection.execute("DROP TRIGGER reject_draft_migration")
        connection.commit()
    initialize_database()
    assert current(api)["extracted"]["title"] == "合成草稿a"


def test_first_schedule_edit_uses_schema_default_for_legacy_missing_field(api):
    response = edit(api, {"schedule": [{"name": "新赛程"}]},
                    expected_values={"schedule": []})
    assert response.status_code == 200, response.text
    assert history(api)["items"][0]["old_value"] == []

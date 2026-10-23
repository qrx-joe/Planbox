"""人工更正/撤回使用临时 SQLite；不运行应用 lifespan 或外部模型。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.db.database import connect, initialize_database


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.app.api import events, evidences

    monkeypatch.setattr(settings, "database_path", tmp_path / "corrections.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    initialize_database()
    with connect() as connection:
        for identifier in ("a", "b"):
            connection.execute(
                "INSERT INTO events (id,title,organizer,status,created_at,updated_at) "
                "VALUES (?, ?, '甲机构', 'interested', 'now', 'now')", (identifier, identifier),
            )
        connection.execute(
            "INSERT INTO source_items "
            "(id,source_type,raw_text,captured_at,parse_status,created_at,updated_at) "
            "VALUES ('src','text','甲机构宣布报名截止2027年1月5日','now','parsed','now','now')"
        )
        connection.execute("INSERT INTO event_sources VALUES ('a','src','now')")
        connection.commit()
    app = FastAPI()
    app.include_router(events.router, prefix="/api/events")
    app.include_router(evidences.router, prefix="/api/evidences")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def history(api, event="a"):
    response = api.get(f"/api/events/{event}/corrections")
    assert response.status_code == 200, response.text
    return response.json()


def correct(api, **values):
    response = api.patch("/api/events/a", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def test_correction_records_old_new_reason_source_and_server_actor(api):
    correct(api, organizer="乙机构", correction_reason="核对另一份通知", source_item_id="src")
    item = history(api)["items"][0]
    assert item["old_value"] == "甲机构" and item["new_value"] == "乙机构"
    assert item["reason"] == "核对另一份通知" and item["source_item_id"] == "src"
    assert item["actor"] == "local_user_unverified"
    assert item["can_retract"] and item["action"] == "correction"


def test_legacy_patch_still_audited_without_inventing_reason(api):
    correct(api, organizer="乙机构")
    assert history(api)["items"][0]["reason"] is None


def test_unchanged_patch_does_not_create_history(api):
    correct(api, organizer="甲机构")
    assert history(api)["total"] == 0


def test_retraction_is_append_only_and_retry_does_not_undo_newer_work(api):
    correct(api, organizer="乙机构")
    original = history(api)["items"][0]
    url = f"/api/events/a/corrections/{original['operation_id']}/retract"
    result = api.post(url, json={"reason": "误改，恢复原值"})
    assert result.status_code == 200, result.text
    assert result.json()["organizer"] == "甲机构"
    correct(api, organizer="丙机构")
    retry = api.post(url, json={"reason": "重试"})
    assert retry.status_code == 200
    assert retry.json()["organizer"] == "丙机构"
    items = history(api)["items"]
    assert len(items) == 3
    undo = next(item for item in items if item["action"] == "retraction")
    assert undo["reverts_id"] == original["id"]
    assert not any(item["can_retract"] for item in items if item["action"] == "retraction")


def test_aba_change_blocks_old_retraction_even_when_value_matches(api):
    correct(api, organizer="乙机构")
    original = history(api)["items"][0]
    correct(api, organizer="丙机构")
    correct(api, organizer="乙机构")
    response = api.post(
        f"/api/events/a/corrections/{original['operation_id']}/retract", json={"reason": "撤回"},
    )
    assert response.status_code == 409
    assert api.get("/api/events/a").json()["organizer"] == "乙机构"


def test_stale_revision_and_expected_value_cannot_overwrite(api):
    correct(api, organizer="乙机构", expected_revision=0, expected_values={"organizer": "甲机构"})
    response = api.patch("/api/events/a", json={"organizer": "丙机构", "expected_revision": 0})
    assert response.status_code == 409
    response = api.patch("/api/events/a", json={
        "organizer": "丙机构", "expected_values": {"organizer": "旧值"},
    })
    assert response.status_code == 409
    assert history(api)["total"] == 1


@pytest.mark.parametrize("event,source", [("b", "src"), ("a", "missing")])
def test_source_must_belong_to_event(api, event, source):
    response = api.patch(f"/api/events/{event}", json={
        "organizer": "乙机构", "source_item_id": source,
    })
    assert response.status_code == 400
    assert history(api, event)["total"] == 0


def test_history_insert_failure_rolls_back_field_and_status(api):
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_history BEFORE INSERT ON event_corrections "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
        connection.commit()
    response = api.patch("/api/events/a", json={"organizer": "乙机构", "status": "done"})
    assert response.status_code == 500
    value = api.get("/api/events/a").json()
    assert value["organizer"] == "甲机构" and value["status"] == "interested"


def test_multifield_time_change_and_retraction_are_atomic(api):
    correct(api, event_start_at="2027-01-01", event_end_at="2027-01-02")
    original = history(api)["items"][0]
    correct(api, event_start_at="2026-12-31")
    response = api.post(
        f"/api/events/a/corrections/{original['operation_id']}/retract", json={"reason": "撤回"},
    )
    assert response.status_code == 409
    assert api.get("/api/events/a").json()["event_end_at"] == "2027-01-02"


def test_retraction_cannot_create_invalid_interval(api):
    correct(api, event_start_at="2027-01-03", event_end_at="2027-01-05")
    correct(api, event_start_at="2027-01-01")
    changed_start = history(api)["items"][0]
    correct(api, event_end_at="2027-01-02")
    response = api.post(
        f"/api/events/a/corrections/{changed_start['operation_id']}/retract",
        json={"reason": "恢复旧开始"},
    )
    assert response.status_code == 400
    assert api.get("/api/events/a").json()["event_start_at"] == "2027-01-01"


def test_null_boolean_and_schedule_history_keep_types(api):
    correct(api, is_online=False, schedule=[{"name": "初赛", "start_at": "2027-01-01"}])
    items = {item["field_name"]: item for item in history(api)["items"]}
    assert items["is_online"]["new_value"] is False
    assert items["schedule"]["new_value"][0]["name"] == "初赛"
    correct(api, organizer=None)
    assert history(api)["items"][0]["new_value"] is None


def test_history_is_entity_scoped_paginated_and_unknown_returns_404(api):
    correct(api, organizer="乙机构", location="上海")
    assert history(api, "b")["items"] == []
    response = api.get("/api/events/a/corrections", params={"limit": 1, "offset": 1}).json()
    assert response["total"] == 2 and len(response["items"]) == 1
    assert response["items"][0]["operation_field_count"] == 2
    assert api.get("/api/events/missing/corrections").status_code == 404


def test_history_migration_repeatable_and_does_not_rewrite_legacy_rows(api):
    initialize_database()
    initialize_database()
    with connect() as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row["version"] for row in versions] == [
            "0001_event_corrections", "0002_draft_corrections",
        ]
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cross_entity_retraction_is_not_found(api):
    correct(api, organizer="乙机构")
    op = history(api)["items"][0]["operation_id"]
    response = api.post(f"/api/events/b/corrections/{op}/retract", json={"reason": "撤回"})
    assert response.status_code == 404


def test_merge_keeps_original_history_but_cannot_retract_it_onto_target(api):
    correct(api, organizer="乙机构")
    original = history(api)["items"][0]
    response = api.post("/api/events/merge", json={"target_id": "b", "source_id": "a"})
    assert response.status_code == 200, response.text
    item = history(api, "b")["items"][0]
    assert item["original_event_id"] == "a" and not item["can_retract"]
    result = api.post(
        f"/api/events/b/corrections/{original['operation_id']}/retract", json={"reason": "撤回"},
    )
    assert result.status_code == 409


def test_malformed_legacy_schedule_does_not_break_history_read(api):
    correct(api, schedule=[{"name": "初赛"}])
    with connect() as connection:
        connection.execute("UPDATE events SET schedule_json='bad' WHERE id='a'")
        connection.commit()
    item = history(api)["items"][0]
    assert not item["can_retract"]


def test_retraction_history_failure_rolls_back_restored_value(api):
    correct(api, organizer="乙机构")
    original = history(api)["items"][0]
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_undo BEFORE INSERT ON event_corrections "
            "WHEN NEW.action='retraction' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        connection.commit()
    response = api.post(
        f"/api/events/a/corrections/{original['operation_id']}/retract", json={"reason": "撤回"},
    )
    assert response.status_code == 500
    assert api.get("/api/events/a").json()["organizer"] == "乙机构"
    assert history(api)["total"] == 1


def test_concurrent_edit_of_same_revision_has_one_winner(api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def submit(value):
        barrier.wait(timeout=5)
        return api.patch("/api/events/a", json={
            "organizer": value, "expected_revision": 0,
            "expected_values": {"organizer": "甲机构"},
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["乙机构", "丙机构"]))
    assert sorted(results) == [200, 409]
    assert history(api)["total"] == 1


def test_failed_migration_rolls_back_new_table_and_keeps_legacy_data(api):
    import sqlite3

    with connect() as connection:
        connection.execute("DROP TABLE event_corrections")
        connection.execute("DELETE FROM schema_migrations")
        connection.execute(
            "CREATE TRIGGER reject_migration BEFORE INSERT ON schema_migrations "
            "BEGIN SELECT RAISE(ABORT,'synthetic migration failure'); END"
        )
        before = [tuple(row) for row in connection.execute("SELECT * FROM events ORDER BY id")]
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        initialize_database()
    with connect() as connection:
        after = [tuple(row) for row in connection.execute("SELECT * FROM events ORDER BY id")]
        assert after == before
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_corrections'",
        ).fetchone() is None


def test_malformed_schedule_can_be_corrected_while_preserving_old_raw_value(api):
    with connect() as connection:
        connection.execute("UPDATE events SET schedule_json='bad' WHERE id='a'")
        connection.commit()
    correct(api, schedule=[{"name": "初赛"}])
    item = history(api)["items"][0]
    assert item["old_value"] == "bad"
    response = api.post(
        f"/api/events/a/corrections/{item['operation_id']}/retract", json={"reason": "撤回"},
    )
    assert response.status_code == 400
    assert api.get("/api/events/a").json()["schedule"] == [{"name": "初赛"}]

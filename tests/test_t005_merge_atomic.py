"""T-005 合并原子性/外键/备份恢复回归：显式收集，临时 SQLite，无外网与真实模型。"""

import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.db.database import connect, initialize_database


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.app.api import events

    monkeypatch.setattr(settings, "database_path", tmp_path / "merge.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    initialize_database()
    seed_two_events()
    app = FastAPI()
    app.include_router(events.router, prefix="/api/events")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def seed_two_events():
    """目标活动缺地点，来源活动带地点、证据、提醒与评分缓存。"""
    with connect() as connection:
        for suffix in ("t", "s"):
            connection.execute(
                "INSERT INTO source_items "
                "(id,source_type,raw_text,captured_at,parse_status,created_at,updated_at) "
                "VALUES (?,'text','合成正文','2026-11-01T09:00:00+08:00','parsed','now','now')",
                (f"src-{suffix}",),
            )
            connection.execute(
                "INSERT INTO events (id,title,organizer,status,created_at,updated_at) "
                "VALUES (?,?,'目标主办方','interested','now','now')",
                (f"evt-{suffix}", "目标活动" if suffix == "t" else "来源活动"),
            )
            connection.execute(
                "INSERT INTO event_sources (event_id,source_item_id,created_at) "
                "VALUES (?,?,'now')",
                (f"evt-{suffix}", f"src-{suffix}"),
            )
        connection.execute("UPDATE events SET organizer=NULL WHERE id='evt-s'")
        connection.execute("UPDATE events SET location='上海' WHERE id='evt-s'")
        connection.execute(
            "INSERT INTO evidences "
            "(id,event_id,source_item_id,field_name,field_value,evidence_text,created_at) "
            "VALUES ('evd-1','evt-s','src-s','location','上海','地点：上海','now')"
        )
        connection.execute(
            "INSERT INTO reminders "
            "(id,event_id,reminder_type,trigger_at,channel,status,created_at,updated_at) "
            "VALUES ('rem-1','evt-s','deadline','2026-11-05T09:00:00+08:00',"
            "'local','pending','now','now')"
        )
        connection.execute(
            "INSERT INTO opportunity_scores (id,event_id,created_at,updated_at) "
            "VALUES ('scr-1','evt-s','now','now')"
        )
        connection.commit()


def merge(api):
    return api.post("/api/events/merge", json={"target_id": "evt-t", "source_id": "evt-s"})


def test_merge_failure_before_delete_rolls_back_everything(api):
    """删除源活动前失败：字段补全、来源与证据迁移必须一并回滚，不留半份。"""
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER block_delete BEFORE DELETE ON events "
            "WHEN OLD.id='evt-s' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        connection.commit()
    failed = merge(api)
    assert failed.status_code == 500
    with connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE id='evt-s'").fetchone()[0] == 1, "源活动应保留"
        assert connection.execute(
            "SELECT location FROM events WHERE id='evt-t'").fetchone()[0] is None, "字段补全应回滚"
        assert connection.execute(
            "SELECT event_id FROM evidences WHERE id='evd-1'"
        ).fetchone()[0] == "evt-s", "证据不应迁移"
        assert connection.execute(
            "SELECT COUNT(*) FROM event_sources WHERE event_id='evt-t'"
        ).fetchone()[0] == 1, "来源不应迁移"
        assert connection.execute(
            "SELECT event_id FROM reminders WHERE id='rem-1'"
        ).fetchone()[0] == "evt-s", "提醒不应迁移"


def test_merge_failure_during_transfer_rolls_back_field_updates(api):
    """证据改挂阶段失败：此前已提交的字段补全同样必须回滚。"""
    with connect() as connection:
        connection.execute(
            "CREATE TRIGGER block_transfer BEFORE UPDATE ON evidences "
            "WHEN NEW.event_id='evt-t' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        connection.commit()
    failed = merge(api)
    assert failed.status_code == 500
    with connect() as connection:
        assert connection.execute(
            "SELECT location FROM events WHERE id='evt-t'").fetchone()[0] is None, "字段补全应回滚"
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE id='evt-s'").fetchone()[0] == 1, "源活动应保留"


def test_merge_transfers_reminders_evidences_and_sources(api):
    """合并保留提醒与证据：改挂目标活动；评分缓存随源活动清理；来源可多挂。"""
    ok = merge(api)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["deleted_event_id"] == "evt-s"
    assert "location" in body["filled_fields"]
    with connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE id='evt-s'").fetchone()[0] == 0
        assert connection.execute(
            "SELECT event_id FROM reminders WHERE id='rem-1'").fetchone()[0] == "evt-t", "提醒不丢"
        assert connection.execute(
            "SELECT event_id FROM evidences WHERE id='evd-1'").fetchone()[0] == "evt-t"
        assert connection.execute(
            "SELECT COUNT(*) FROM event_sources WHERE event_id='evt-t'").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM opportunity_scores WHERE event_id='evt-s'").fetchone()[0] == 0


def test_foreign_keys_are_enforced(tmp_path, monkeypatch):
    """连接层启用外键：引用不存在的父行在写入时即被拒绝，而不是悄悄产生孤儿。"""
    monkeypatch.setattr(settings, "database_path", tmp_path / "fk.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    initialize_database()
    with connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO evidences (id,source_item_id,field_name,created_at) "
                "VALUES ('evd-x','missing-source','title','now')"
            )


def test_backup_copy_is_complete_and_usable(api, tmp_path, monkeypatch):
    """备份演练：在线一致副本 → 迁移可重入 → 数据完整 → 副本可继续写入。"""
    backup_path = tmp_path / "backup.db"
    with (
        sqlite3.connect(settings.database_path) as source,
        sqlite3.connect(backup_path) as target,
    ):
        source.backup(target)

    monkeypatch.setattr(settings, "database_path", backup_path)
    initialize_database()  # 在副本上重入迁移，模拟恢复流程

    listed = api.get("/api/events?paginated=false")
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()} == {"evt-t", "evt-s"}

    with connect() as connection:  # 副本含全部种子数据
        assert connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidences").fetchone()[0] == 1

    patched = api.patch("/api/events/evt-t", json={"organizer": "恢复后修改"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["organizer"] == "恢复后修改"


def test_cross_process_concurrent_confirm_single_event(tmp_path, monkeypatch):
    """独立进程与主进程先后/并发确认同一草稿：恰好建档一次，双方拿到同一活动。"""
    monkeypatch.setattr(settings, "database_path", tmp_path / "cross.db")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    initialize_database()
    with connect() as connection:
        connection.execute(
            "INSERT INTO source_items "
            "(id,source_type,raw_text,captured_at,parse_status,created_at,updated_at) "
            "VALUES ('src-x','text','合成正文','2026-11-01T09:00:00+08:00','parsed','now','now')"
        )
        connection.execute(
            "INSERT INTO extraction_results "
            "(id,source_item_id,status,extracted_json,missing_fields,"
            "confidence,created_at,updated_at) "
            "VALUES ('draft-x','src-x','pending_review',?,'[]',0.5,'now','now')",
            (json.dumps({"title": "跨进程草稿"}, ensure_ascii=False),),
        )
        connection.commit()

    gate = tmp_path / "gate.txt"
    script = (
        "import sys\n"
        f"sys.path.insert(0, r'{PROJECT_ROOT}')\n"
        "from pathlib import Path\n"
        "from backend.app.core.config import settings\n"
        f"settings.database_path = Path(r'{settings.database_path}')\n"
        f"settings.data_dir = Path(r'{settings.data_dir}')\n"
        f"settings.upload_dir = Path(r'{settings.upload_dir}')\n"
        "from backend.app.schemas.event import EventConfirm\n"
        "from backend.app.services.event_service import EventService\n"
        f"Path(r'{gate}').touch()\n"
        "result = EventService().confirm_extraction('draft-x', EventConfirm(status='interested'))\n"
        "print(result.id)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        import time

        for _ in range(100):  # 等子进程就绪后主进程立即确认，制造竞争窗口
            if gate.exists():
                break
            time.sleep(0.02)
        from backend.app.schemas.event import EventConfirm
        from backend.app.services.event_service import EventService

        main_result = EventService().confirm_extraction(
            "draft-x", EventConfirm(status="interested")
        )
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, stderr
        child_event_id = stdout.strip().splitlines()[-1]
        assert child_event_id == main_result.id, "两个进程必须拿到同一活动"
        with connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        if child.poll() is None:  # pragma: no cover - 超时兜底
            child.kill()

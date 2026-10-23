"""浏览器回归专用服务；只创建自己的临时库，不使用用户数据。

正常退出由 TemporaryDirectory 清理；强制中断可能留下打印的路径，需核对后清理。
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    import uvicorn

    from backend.app.core.config import settings
    from backend.app.db.database import connect, initialize_database

    root = PROJECT_ROOT / ".testdata"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t003-browser-", dir=root) as directory:
        location = Path(directory)
        print(f"SYNTHETIC_DATA_DIR={location}", flush=True)
        settings.data_dir = location
        settings.database_path = location / "synthetic.db"
        settings.upload_dir = location / "uploads"
        settings.llm_provider = "fake"
        settings.cors_allow_origins = ["http://127.0.0.1:5176"]
        initialize_database()
        with connect() as connection:
            for suffix, organizer, token in [("a", "主办方甲", "A限定片段"),
                                              ("b", "主办方乙", "B限定片段")]:
                text = "合成背景。" * 100 + f"{token}，{organizer}，报名截止2027年1月5日。"
                connection.execute(
                    "INSERT INTO source_items "
                    "(id,source_type,raw_text,title,captured_at,parse_status,"
                    "created_at,updated_at) "
                    "VALUES (?,'text',?,?,'now','parsed','now','now')",
                    (f"source-{suffix}", text, f"合成来源{suffix}"),
                )
                connection.execute(
                    "INSERT INTO events "
                    "(id,title,organizer,registration_deadline_at,status,created_at,updated_at) "
                    "VALUES (?,?,?,'2027-01-05','interested','now','now')",
                    (f"event-{suffix}", f"合成活动{suffix.upper()}", organizer),
                )
                connection.execute(
                    "INSERT INTO event_sources VALUES (?,?,'now')",
                    (f"event-{suffix}", f"source-{suffix}"),
                )
                for field, value in [("organizer", organizer),
                                     ("registration_deadline_at", "2027-01-05")]:
                    connection.execute(
                        "INSERT INTO evidences "
                        "(id,event_id,source_item_id,field_name,field_value,"
                        "evidence_text,created_at) "
                        "VALUES (?,?,?,?,?,'不应直接展示的旧片段','now')",
                        (f"evidence-{suffix}-{field}", f"event-{suffix}",
                         f"source-{suffix}", field, value),
                    )
            connection.commit()
            connection.execute(
                "INSERT INTO source_items "
                "(id,source_type,raw_text,title,captured_at,parse_status,created_at,updated_at) "
                "VALUES ('source-review','text',?,'合成待核对来源',"
                "'2026-10-23T09:00:00+08:00','parsed','now','now')",
                ("合成待核对草稿。甲机构宣布报名截止2027年1月5日，举办安排待补充。",),
            )
            connection.execute(
                "INSERT INTO extraction_results "
                "(id,source_item_id,status,extracted_json,missing_fields,confidence,"
                "created_at,updated_at) VALUES "
                "('review-a','source-review','pending_review',?,'[\"registration_deadline_at\"]',"
                "0.5,'now','now')",
                (json.dumps({"title": "合成待核对草稿", "organizer": "甲机构",
                             "registration_deadline_at": "1月5日"}, ensure_ascii=False),),
            )
            connection.commit()
        from backend.app.main import app

        uvicorn.run(app, host="127.0.0.1", port=8023, log_level="warning")


if __name__ == "__main__":
    main()

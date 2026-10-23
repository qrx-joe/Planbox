"""T-003 的增量迁移；仅新增历史表，不回填或改写已有活动/来源。"""

import sqlite3


def migrate_evidence_history(connection: sqlite3.Connection) -> None:
    """在调用方事务内安装版本 0001；重复启动只检查版本，不销毁历史。

    原有无版本的补列逻辑仍由 database.py 管理，本版本不声称它们已有迁移审计。
    审计表保留 original_event_id：合并可以迁移归属，原活动身份不被覆盖。
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    version = "0001_event_corrections"
    if connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,),
    ).fetchone():
        return
    connection.execute("""
        CREATE TABLE event_corrections (
            id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            original_event_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value_json TEXT NOT NULL,
            new_value_json TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT,
            source_item_id TEXT,
            action TEXT NOT NULL CHECK (action IN ('correction', 'retraction')),
            reverts_id TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    connection.execute(
        "CREATE INDEX idx_event_corrections_event ON event_corrections(event_id)"
    )
    connection.execute(
        "CREATE INDEX idx_event_corrections_operation ON event_corrections(operation_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (version,),
    )


def migrate_draft_history(connection: sqlite3.Connection) -> None:
    """0002 只新增草稿审计；原始来源、旧抽取 JSON 与活动记录不回填。"""
    version = "0002_draft_corrections"
    if connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,),
    ).fetchone():
        return
    connection.execute("""
        CREATE TABLE draft_corrections (
            id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
            extraction_result_id TEXT NOT NULL, field_name TEXT NOT NULL,
            old_value_json TEXT NOT NULL, new_value_json TEXT NOT NULL,
            actor TEXT NOT NULL, reason TEXT, source_item_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('correction', 'retraction')),
            reverts_id TEXT UNIQUE, created_at TEXT NOT NULL
        )
    """)
    connection.execute(
        "CREATE INDEX idx_draft_corrections_extraction ON draft_corrections(extraction_result_id)"
    )
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (version,),
    )

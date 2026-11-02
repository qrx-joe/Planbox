import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from backend.app.core.config import settings
from backend.app.db.evidence_history import migrate_draft_history, migrate_evidence_history

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_items (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_url TEXT,
  raw_text TEXT,
  title TEXT,
  published_at TEXT,
  captured_at TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  parse_error TEXT,
  ingest_state TEXT,
  source_meta TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_assets (
  id TEXT PRIMARY KEY,
  source_item_id TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  file_path TEXT NOT NULL,
  original_filename TEXT,
  mime_type TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);

CREATE TABLE IF NOT EXISTS extraction_results (
  id TEXT PRIMARY KEY,
  source_item_id TEXT NOT NULL,
  status TEXT NOT NULL,
  extracted_json TEXT NOT NULL,
  missing_fields TEXT,
  confidence REAL,
  confirmed_event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  event_type TEXT,
  organizer TEXT,
  registration_url TEXT,
  registration_start_at TEXT,
  registration_deadline_at TEXT,
  event_start_at TEXT,
  event_end_at TEXT,
  location TEXT,
  is_online INTEGER,
  schedule_json TEXT,
  fee TEXT,
  prize TEXT,
  team_required TEXT,
  audience TEXT,
  status TEXT NOT NULL,
  missing_fields TEXT,
  confidence REAL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_sources (
  event_id TEXT NOT NULL,
  source_item_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (event_id, source_item_id),
  FOREIGN KEY (event_id) REFERENCES events(id),
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);

CREATE TABLE IF NOT EXISTS evidences (
  id TEXT PRIMARY KEY,
  event_id TEXT,
  extraction_result_id TEXT,
  source_item_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  field_value TEXT,
  evidence_text TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id),
  FOREIGN KEY (extraction_result_id) REFERENCES extraction_results(id),
  FOREIGN KEY (source_item_id) REFERENCES source_items(id)
);

CREATE INDEX IF NOT EXISTS idx_source_items_created_at
  ON source_items(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_assets_source_item_id
  ON source_assets(source_item_id);

CREATE INDEX IF NOT EXISTS idx_extraction_results_status
  ON extraction_results(status);

CREATE INDEX IF NOT EXISTS idx_extraction_results_source_item_id
  ON extraction_results(source_item_id);

CREATE INDEX IF NOT EXISTS idx_events_status
  ON events(status);

CREATE INDEX IF NOT EXISTS idx_events_registration_deadline_at
  ON events(registration_deadline_at);

CREATE INDEX IF NOT EXISTS idx_evidences_event_id
  ON evidences(event_id);

CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  reminder_type TEXT NOT NULL,
  trigger_at TEXT NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS opportunity_scores (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  fit_score INTEGER,
  urgency_score INTEGER,
  portfolio_value INTEGER,
  networking_value INTEGER,
  learning_value INTEGER,
  time_cost TEXT,
  recommendation TEXT,
  reason_to_join TEXT,
  reason_to_skip TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS user_preferences (
  id TEXT PRIMARY KEY,
  goals TEXT,
  interested_topics TEXT,
  blocked_topics TEXT,
  preferred_locations TEXT,
  online_only INTEGER,
  available_time TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidences_extraction_result_id
  ON evidences(extraction_result_id);

CREATE INDEX IF NOT EXISTS idx_reminders_event_id
  ON reminders(event_id);

CREATE INDEX IF NOT EXISTS idx_reminders_status
  ON reminders(status);

CREATE INDEX IF NOT EXISTS idx_opportunity_scores_event_id
  ON opportunity_scores(event_id);
"""


def initialize_database() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute("BEGIN")
        _run_migrations(connection)
        migrate_evidence_history(connection)
        migrate_draft_history(connection)
        connection.commit()


def _existing_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _run_migrations(connection: sqlite3.Connection) -> None:
    """为已存在的库补齐新版本引入的列。

    SCHEMA 里的 CREATE TABLE IF NOT EXISTS 对已存在的表不会生效，
    所以后续新增字段必须在这里显式补，否则老库读新字段会报错。
    """
    migrations: tuple[tuple[str, str, str], ...] = (
        # (表名, 新列名, ALTER 语句)
        ("events", "notes", "ALTER TABLE events ADD COLUMN notes TEXT"),
        (
            "source_items",
            "ingest_state",
            "ALTER TABLE source_items ADD COLUMN ingest_state TEXT",
        ),
        (
            "source_items",
            "source_meta",
            "ALTER TABLE source_items ADD COLUMN source_meta TEXT",
        ),
        (
            # 确认时记录建出的活动，供响应丢失后的重复确认幂等返回原活动
            "extraction_results",
            "confirmed_event_id",
            "ALTER TABLE extraction_results ADD COLUMN confirmed_event_id TEXT",
        ),
    )
    for table, column, alter in migrations:
        if column not in _existing_columns(connection, table):
            connection.execute(alter)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    # SQLite 默认不启用外键；打开后引用缺失在写入时即被拒绝，避免悄悄产生孤儿行。
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()

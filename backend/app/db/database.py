import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from backend.app.core.config import settings

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

CREATE INDEX IF NOT EXISTS idx_evidences_extraction_result_id
  ON evidences(extraction_result_id);
"""


def initialize_database() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)
        connection.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()

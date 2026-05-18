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

CREATE INDEX IF NOT EXISTS idx_source_items_created_at
  ON source_items(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_assets_source_item_id
  ON source_assets(source_item_id);
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

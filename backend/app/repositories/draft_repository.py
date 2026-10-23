"""草稿编辑与确认的事务边界；原始来源不由 Review 修改。"""

import sqlite3
from contextlib import contextmanager

from backend.app.db.database import connect
from backend.app.repositories.extraction_repository import ExtractionRepository


class DraftRepository:
    @contextmanager
    def edit(self, extraction_id: str):
        """同一短写事务读取并写入候选/证据/审计；异常全部回滚。"""
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = ExtractionRepository().get(extraction_id, connection)
                if row is None:
                    raise KeyError(extraction_id)
                yield connection, row
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def rows(self, connection: sqlite3.Connection, extraction_id: str) -> list[dict]:
        return [dict(row) for row in connection.execute(
            "SELECT rowid AS sequence,* FROM draft_corrections "
            "WHERE extraction_result_id=? ORDER BY rowid DESC", (extraction_id,),
        ).fetchall()]

    def read(self, extraction_id: str) -> tuple[dict, list[dict]]:
        with connect() as connection:
            connection.execute("BEGIN")
            row = ExtractionRepository().get(extraction_id, connection)
            if row is None:
                raise KeyError(extraction_id)
            return row, self.rows(connection, extraction_id)

    def source(self, connection: sqlite3.Connection, source_id: str) -> dict:
        row = connection.execute("SELECT * FROM source_items WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("草稿原始来源不可读取，请核对收集记录")
        return dict(row)

    def save(self, connection: sqlite3.Connection, row: dict) -> None:
        connection.execute(
            "UPDATE extraction_results SET extracted_json=:extracted_json, "
            "missing_fields=:missing_fields, updated_at=:updated_at WHERE id=:id", row,
        )

    def append(self, connection: sqlite3.Connection, row: dict) -> None:
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        connection.execute(
            f"INSERT INTO draft_corrections ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    def evidence_pairs(self, connection: sqlite3.Connection, extraction_id: str) -> set[tuple]:
        return {tuple(row) for row in connection.execute(
            "SELECT field_name,field_value FROM evidences WHERE extraction_result_id=?",
            (extraction_id,),
        ).fetchall()}

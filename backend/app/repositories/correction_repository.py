"""更正的连接与 SQL 边界；用例共享一条 BEGIN IMMEDIATE 事务连接。"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from backend.app.core.evidence import FACT_FIELDS
from backend.app.db.database import connect

EDIT_COLUMNS = {*FACT_FIELDS, "schedule_json", "notes", "status", "updated_at"}


class CorrectionRepository:
    @contextmanager
    def edit(self, event_id: str) -> Iterator[tuple[sqlite3.Connection, dict[str, object]]]:
        """串行化短写事务；任一步异常都回滚字段与审计，连接不外泄。"""
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
                if row is None:
                    raise KeyError(event_id)
                yield connection, dict(row)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def rows(self, connection: sqlite3.Connection, event_id: str) -> list[dict[str, object]]:
        rows = connection.execute(
            "SELECT rowid AS sequence, * FROM event_corrections "
            "WHERE event_id=? ORDER BY rowid DESC",
            (event_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list(self, event_id: str) -> tuple[dict[str, object], list[dict[str, object]]]:
        """同一读事务获得当前值和历史，避免并发写入使两者不一致。"""
        with connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            return dict(row), self.rows(connection, event_id)

    def update(
        self, connection: sqlite3.Connection, event_id: str, values: dict[str, object],
    ) -> None:
        if not set(values) <= EDIT_COLUMNS:
            raise ValueError("不支持的更正字段")
        assignments = ", ".join(f"{column}=?" for column in values)
        connection.execute(
            f"UPDATE events SET {assignments} WHERE id=?", (*values.values(), event_id),
        )

    def append(self, connection: sqlite3.Connection, row: dict[str, object]) -> None:
        columns = ",".join(row)
        slots = ",".join("?" for _ in row)
        connection.execute(
            f"INSERT INTO event_corrections ({columns}) VALUES ({slots})", tuple(row.values()),
        )

    def source_belongs(self, connection: sqlite3.Connection, event_id: str, source_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM event_sources JOIN source_items ON source_items.id=source_item_id "
            "WHERE event_id=? AND source_item_id=?", (event_id, source_id),
        ).fetchone() is not None


def field_value(row: dict[str, object], field: str) -> object:
    """活动列转为审计 JSON 值；保留 null、布尔、赛程类型及原始时间精度。"""
    if field == "schedule":
        raw = str(row.get("schedule_json") or "[]")
        try:
            return json.loads(raw)
        except ValueError:
            # 旧坏值作为原字符串留在审计中，不能为了修复而销毁或猜测原值。
            return raw
    if field == "is_online":
        value = row.get(field)
        return bool(value) if value is not None else None
    return row.get(field)

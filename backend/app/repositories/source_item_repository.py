import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

from backend.app.db.database import connect


class SourceStateConflict(ValueError):
    """来源当前状态不允许执行本次抽取或正文修改。"""


class SourceItemRepository:
    @contextmanager
    def edit(self, source_item_id: str) -> Iterator[tuple[sqlite3.Connection, dict[str, object]]]:
        """短写事务内读取来源；不存在抛 KeyError，异常回滚，不跨网络调用持锁。"""
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM source_items WHERE id = ?", (source_item_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(source_item_id)
                yield connection, dict(row)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def create(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO source_items ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            return self._get_or_raise(connection, str(values["id"]))

    def list(self) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_items ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, source_item_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_items WHERE id = ?",
                (source_item_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_content(
        self,
        *,
        source_item_id: str,
        raw_text: str,
        title: str | None,
        published_at: str | None,
        ingest_state: str,
        parse_status: str,
        updated_at: str,
    ) -> dict[str, object]:
        """补全文本/标题/发布时间；相同输入保留解析状态和错误。

        输入改变才重置解析状态；pending 期间拒绝改变输入，避免迟到抽取覆盖新正文。
        比较与写入共用短事务；缺失抛 KeyError，处理中改内容抛 SourceStateConflict。
        """
        with self.edit(source_item_id) as (connection, current):
            changed = (
                raw_text != current["raw_text"]
                or (title is not None and title != current["title"])
                or (published_at is not None and published_at != current["published_at"])
            )
            if changed and current["parse_status"] == "pending":
                raise SourceStateConflict("正在抽取，请等待本次抽取结束后再修改正文或来源信息")
            connection.execute(
                """
                UPDATE source_items
                SET raw_text = ?,
                    title = COALESCE(?, title),
                    published_at = COALESCE(?, published_at),
                    ingest_state = ?,
                    parse_status = ?,
                    parse_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    raw_text,
                    title,
                    published_at,
                    ingest_state,
                    parse_status if changed else current["parse_status"],
                    None if changed else current["parse_error"],
                    updated_at,
                    source_item_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM source_items WHERE id = ?",
                (source_item_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Source item was not persisted")
        return dict(row)

    def update_parse_status(
        self,
        source_item_id: str,
        parse_status: str,
        updated_at: str,
        parse_error: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """写解析状态与安全错误；传入连接时由调用方提交，否则独立提交。"""
        with (nullcontext(connection) if connection is not None else connect()) as active:
            active.execute(
                """
                UPDATE source_items
                SET parse_status = ?, parse_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (parse_status, parse_error, updated_at, source_item_id),
            )
            if connection is None:
                active.commit()

    def create_asset(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO source_assets ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            row = connection.execute(
                "SELECT * FROM source_assets WHERE id = ?",
                (values["id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("Asset was not persisted")
            return dict(row)

    def _get_or_raise(
        self, connection: sqlite3.Connection, source_item_id: str
    ) -> dict[str, object]:
        row = connection.execute(
            "SELECT * FROM source_items WHERE id = ?",
            (source_item_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Source item was not persisted")
        return dict(row)

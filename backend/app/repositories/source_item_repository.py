import sqlite3

from backend.app.db.database import connect


class SourceItemRepository:
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

    def update_parse_status(
        self,
        source_item_id: str,
        parse_status: str,
        updated_at: str,
        parse_error: str | None = None,
    ) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE source_items
                SET parse_status = ?, parse_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (parse_status, parse_error, updated_at, source_item_id),
            )
            connection.commit()

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

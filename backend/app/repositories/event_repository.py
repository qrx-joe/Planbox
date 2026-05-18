from backend.app.db.database import connect


class EventRepository:
    def create(self, values: dict[str, object], source_item_id: str) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO events ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.execute(
                """
                INSERT INTO event_sources (event_id, source_item_id, created_at)
                VALUES (?, ?, ?)
                """,
                (values["id"], source_item_id, values["created_at"]),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?",
                (values["id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("Event was not persisted")
            return dict(row)

    def list(self) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                ORDER BY
                  CASE WHEN registration_deadline_at IS NULL THEN 1 ELSE 0 END,
                  registration_deadline_at ASC,
                  created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]


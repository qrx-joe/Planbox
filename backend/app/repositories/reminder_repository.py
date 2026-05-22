from __future__ import annotations

from backend.app.db.database import connect


class ReminderRepository:
    def create(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO reminders ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            return self._get_or_raise(str(values["id"]))

    def list(self) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT reminders.*, events.title AS event_title
                FROM reminders
                JOIN events ON events.id = reminders.event_id
                ORDER BY reminders.trigger_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_by_event(self, event_id: str) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders WHERE event_id = ? ORDER BY trigger_at ASC
                """,
                (event_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, reminder_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_status(self, reminder_id: str, status: str, updated_at: str) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, updated_at, reminder_id),
            )
            connection.commit()

    def delete(self, reminder_id: str) -> None:
        with connect() as connection:
            connection.execute(
                "DELETE FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            connection.commit()

    def _get_or_raise(self, reminder_id: str) -> dict[str, object]:
        row = self.get(reminder_id)
        if row is None:
            raise RuntimeError("Reminder was not persisted")
        return row

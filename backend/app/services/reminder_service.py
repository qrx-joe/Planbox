from __future__ import annotations

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.reminder_repository import ReminderRepository
from backend.app.schemas.reminder import ReminderCreate, ReminderRead, ReminderUpdate


class ReminderService:
    def __init__(self, repository: ReminderRepository | None = None) -> None:
        self.repository = repository or ReminderRepository()

    def create(self, payload: ReminderCreate) -> ReminderRead:
        timestamp = now_iso()
        row = self.repository.create(
            {
                "id": new_id("rem"),
                "event_id": payload.event_id,
                "reminder_type": payload.reminder_type,
                "trigger_at": payload.trigger_at,
                "channel": payload.channel,
                "status": "pending",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return self._to_read(row)

    def list(self) -> list[ReminderRead]:
        return [self._to_read(row) for row in self.repository.list()]

    def list_by_event(self, event_id: str) -> list[ReminderRead]:
        return [self._to_read(row) for row in self.repository.list_by_event(event_id)]

    def update(self, reminder_id: str, payload: ReminderUpdate) -> ReminderRead | None:
        row = self.repository.get(reminder_id)
        if row is None:
            return None

        if payload.status is not None:
            self.repository.update_status(reminder_id, payload.status, now_iso())
            row = self.repository.get(reminder_id)

        return self._to_read(row) if row else None

    def delete(self, reminder_id: str) -> bool:
        if self.repository.get(reminder_id) is None:
            return False
        self.repository.delete(reminder_id)
        return True

    def _to_read(self, row: dict[str, object]) -> ReminderRead:
        return ReminderRead(
            id=str(row["id"]),
            event_id=str(row["event_id"]),
            event_title=row.get("event_title"),
            reminder_type=row["reminder_type"],
            trigger_at=str(row["trigger_at"]),
            channel=row["channel"],
            status=row["status"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

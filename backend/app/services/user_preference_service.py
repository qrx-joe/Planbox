from __future__ import annotations

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.user_preference_repository import UserPreferenceRepository
from backend.app.schemas.user_preference import UserPreferenceCreate, UserPreferenceRead


class UserPreferenceService:
    def __init__(self, repository: UserPreferenceRepository | None = None) -> None:
        self.repository = repository or UserPreferenceRepository()

    def create(self, payload: UserPreferenceCreate) -> UserPreferenceRead:
        timestamp = now_iso()
        row = self.repository.create(
            {
                "id": new_id("usr"),
                "goals": payload.goals,
                "interested_topics": payload.interested_topics,
                "blocked_topics": payload.blocked_topics,
                "preferred_locations": payload.preferred_locations,
                "online_only": int(payload.online_only) if payload.online_only is not None else None,
                "available_time": payload.available_time,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return self._to_read(row)

    def get_latest(self) -> UserPreferenceRead | None:
        row = self.repository.get_latest()
        return self._to_read(row) if row else None

    def update(self, preference_id: str, payload: UserPreferenceCreate) -> UserPreferenceRead | None:
        row = self.repository.get(preference_id)
        if row is None:
            return None

        values: dict[str, object] = {"updated_at": now_iso()}
        if payload.goals is not None:
            values["goals"] = payload.goals
        if payload.interested_topics is not None:
            values["interested_topics"] = payload.interested_topics
        if payload.blocked_topics is not None:
            values["blocked_topics"] = payload.blocked_topics
        if payload.preferred_locations is not None:
            values["preferred_locations"] = payload.preferred_locations
        if payload.online_only is not None:
            values["online_only"] = int(payload.online_only)
        if payload.available_time is not None:
            values["available_time"] = payload.available_time

        updated = self.repository.update(preference_id, values)
        return self._to_read(updated)

    def _to_read(self, row: dict[str, object]) -> UserPreferenceRead:
        return UserPreferenceRead(
            id=str(row["id"]),
            goals=row.get("goals"),
            interested_topics=row.get("interested_topics"),
            blocked_topics=row.get("blocked_topics"),
            preferred_locations=row.get("preferred_locations"),
            online_only=bool(row["online_only"]) if row.get("online_only") is not None else None,
            available_time=row.get("available_time"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

from __future__ import annotations

from backend.app.db.database import connect


class UserPreferenceRepository:
    def create(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO user_preferences ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            return self._get_or_raise(str(values["id"]))

    def get(self, preference_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_preferences WHERE id = ?",
                (preference_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_latest(self) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_preferences ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def update(self, preference_id: str, values: dict[str, object]) -> dict[str, object]:
        with connect() as connection:
            columns = ", ".join(f"{key} = :{key}" for key in values)
            connection.execute(
                f"UPDATE user_preferences SET {columns} WHERE id = :id",
                {**values, "id": preference_id},
            )
            connection.commit()
            return self._get_or_raise(preference_id)

    def _get_or_raise(self, preference_id: str) -> dict[str, object]:
        row = self.get(preference_id)
        if row is None:
            raise RuntimeError("User preference was not persisted")
        return row

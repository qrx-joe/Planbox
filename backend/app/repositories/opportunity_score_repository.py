from __future__ import annotations

from backend.app.db.database import connect


class OpportunityScoreRepository:
    def create(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO opportunity_scores ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            return self._get_or_raise(str(values["id"]))

    def get_by_event(self, event_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM opportunity_scores WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row else None

    def update(self, event_id: str, values: dict[str, object]) -> dict[str, object]:
        with connect() as connection:
            columns = ", ".join(f"{key} = :{key}" for key in values)
            connection.execute(
                f"UPDATE opportunity_scores SET {columns} WHERE event_id = :event_id",
                {**values, "event_id": event_id},
            )
            connection.commit()
            return self._get_or_raise(event_id)

    def _get_or_raise(self, event_id: str) -> dict[str, object]:
        row = self.get_by_event(event_id)
        if row is None:
            raise RuntimeError("Opportunity score was not persisted")
        return row

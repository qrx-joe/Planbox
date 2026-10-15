import json

from backend.app.db.database import connect


class ExtractionRepository:
    def create(self, values: dict[str, object]) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO extraction_results ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.commit()
            return self._get_or_raise(str(values["id"]))

    def list_pending(self) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  extraction_results.*,
                  source_items.title AS source_title,
                  source_items.source_url AS source_url,
                  source_items.raw_text AS source_text,
                  source_items.source_type AS source_type
                FROM extraction_results
                JOIN source_items ON source_items.id = extraction_results.source_item_id
                WHERE extraction_results.status = 'pending_review'
                ORDER BY extraction_results.created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, extraction_result_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM extraction_results WHERE id = ?",
                (extraction_result_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_status(self, extraction_result_id: str, status: str, updated_at: str) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE extraction_results
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, updated_at, extraction_result_id),
            )
            connection.commit()

    def list_field_names(self, extraction_result_id: str) -> set[str]:
        """该草稿已有的证据字段名，用于确认时避免重复写入。"""
        with connect() as connection:
            rows = connection.execute(
                "SELECT field_name FROM evidences WHERE extraction_result_id = ?",
                (extraction_result_id,),
            ).fetchall()
            return {str(row["field_name"]) for row in rows}

    def attach_evidences_to_event(self, extraction_result_id: str, event_id: str) -> int:
        """确认入库时把草稿证据挂到正式活动上。

        证据在抽取阶段就写入，此时 event_id 为 NULL；不补这一步，
        evidences 表里的记录就永远无法按活动检索。
        """
        with connect() as connection:
            cursor = connection.execute(
                """
                UPDATE evidences
                SET event_id = ?
                WHERE extraction_result_id = ? AND event_id IS NULL
                """,
                (event_id, extraction_result_id),
            )
            connection.commit()
            return cursor.rowcount

    def create_evidences(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        with connect() as connection:
            connection.executemany(
                """
                INSERT INTO evidences (
                  id,
                  event_id,
                  extraction_result_id,
                  source_item_id,
                  field_name,
                  field_value,
                  evidence_text,
                  confidence,
                  created_at
                )
                VALUES (
                  :id,
                  :event_id,
                  :extraction_result_id,
                  :source_item_id,
                  :field_name,
                  :field_value,
                  :evidence_text,
                  :confidence,
                  :created_at
                )
                """,
                rows,
            )
            connection.commit()

    def _get_or_raise(self, extraction_result_id: str) -> dict[str, object]:
        row = self.get(extraction_result_id)
        if row is None:
            raise RuntimeError("Extraction result was not persisted")
        return row


def dumps_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads_json(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    return json.loads(value)


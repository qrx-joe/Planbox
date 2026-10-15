from __future__ import annotations

from backend.app.db.database import connect


class EvidenceRepository:
    def list_by_event(self, event_id: str) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  evidences.*,
                  source_items.title AS source_title,
                  source_items.source_url AS source_url
                FROM evidences
                LEFT JOIN source_items ON source_items.id = evidences.source_item_id
                WHERE evidences.event_id = ?
                ORDER BY evidences.created_at ASC
                """,
                (event_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_by_extraction(self, extraction_result_id: str) -> list[dict[str, object]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT evidences.*, source_items.source_url AS source_url
                FROM evidences
                LEFT JOIN source_items ON source_items.id = evidences.source_item_id
                WHERE evidences.extraction_result_id = ?
                ORDER BY evidences.created_at ASC
                """,
                (extraction_result_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_pending_without_event(self, extraction_result_id: str) -> list[dict[str, object]]:
        """草稿阶段的证据（尚未关联到正式活动）。"""
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidences
                WHERE extraction_result_id = ? AND event_id IS NULL
                ORDER BY created_at ASC
                """,
                (extraction_result_id,),
            ).fetchall()
            return [dict(row) for row in rows]
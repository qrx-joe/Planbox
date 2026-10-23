from __future__ import annotations

from backend.app.core.evidence import FACT_FIELDS
from backend.app.db.database import connect

READ_FIELDS = (*FACT_FIELDS, "schedule_json")


class EvidenceRepository:
    def list_by_event(self, event_id: str) -> list[dict[str, object]]:
        return self._list_with_context("event_id", event_id)

    def list_by_extraction(self, extraction_result_id: str) -> list[dict[str, object]]:
        return self._list_with_context("extraction_result_id", extraction_result_id)

    def _list_with_context(self, column: str, identifier: str) -> list[dict[str, object]]:
        """单查询读取核验所需的原文和当前值；列名只允许内部白名单，无写入副作用。"""
        if column not in {"event_id", "extraction_result_id"}:
            raise ValueError("不支持的证据查询字段")
        fields = ", ".join(f"events.{name} AS current_{name}" for name in READ_FIELDS)
        with connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  evidences.*,
                  source_items.title AS source_title,
                  source_items.source_url AS source_url,
                  source_items.raw_text AS source_text,
                  events.id AS current_event_id,
                  extraction_results.extracted_json AS current_extracted_json,
                  {fields}
                FROM evidences
                LEFT JOIN source_items ON source_items.id = evidences.source_item_id
                LEFT JOIN events ON events.id = evidences.event_id
                LEFT JOIN extraction_results
                  ON extraction_results.id = evidences.extraction_result_id
                WHERE evidences.{column} = ?
                ORDER BY evidences.created_at ASC, evidences.id ASC
                """,
                (identifier,),
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

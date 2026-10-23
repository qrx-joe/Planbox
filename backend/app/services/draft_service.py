"""Review 草稿更正：保存候选不表示确认事实，时间问题保留至确认边界处理。"""

import json

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.draft_repository import DraftRepository
from backend.app.repositories.extraction_repository import ExtractionRepository, dumps_json
from backend.app.schemas.correction import DraftCorrectionList, DraftCorrectionRead
from backend.app.schemas.extraction import DraftEdit, ExtractionResultRead
from backend.app.services.correction_service import CorrectionConflict
from backend.app.services.extraction_service import ExtractionService


class DraftService:
    def __init__(self) -> None:
        self.repository = DraftRepository()
        self.extractions = ExtractionRepository()
        self.rules = ExtractionService()

    def get(self, extraction_id: str) -> ExtractionResultRead:
        row, _ = self.repository.read(extraction_id)
        return self.rules._to_read(row)

    def update(self, extraction_id: str, payload: DraftEdit) -> ExtractionResultRead:
        """只修改 pending_review；事实、定位证据和更正记录同一事务写入。"""
        with self.repository.edit(extraction_id) as (connection, row):
            updated = self.apply_locked(connection, row, payload)
            return self.rules._to_read(updated)

    def apply_locked(self, connection, row: dict, payload: DraftEdit) -> dict:
        """供编辑和兼容旧确认输入共用；调用方必须持有显式事务连接。"""
        if row["status"] != "pending_review":
            raise CorrectionConflict("草稿已确认或放弃，不能再修改")
        history = self.repository.rows(connection, row["id"])
        if payload.expected_revision != len(history):
            raise CorrectionConflict("草稿已被更正，请重新读取后再修改")
        original = json.loads(row["extracted_json"])
        source = self.repository.source(connection, row["source_item_id"])
        baseline = self.rules.prepare_review_draft(original, source)
        original_values = baseline.model_dump()
        for field, value in (payload.expected_values or {}).items():
            if original_values.get(field) != value:
                raise CorrectionConflict("草稿内容已变化，请重新读取后再修改")
        draft = self.rules.prepare_review_draft({**original, **payload.changes}, source)
        if not draft.title.strip():
            raise ValueError("草稿标题不能为空")
        values = draft.model_dump()
        changes = [(field, original_values.get(field), values[field], None)
                   for field in payload.changes if original_values.get(field) != values[field]]
        if not changes:
            return row
        updated = {**row, "extracted_json": dumps_json(values),
                   "missing_fields": dumps_json(draft.missing_fields), "updated_at": now_iso(),
                   "revision": len(history) + len(changes)}
        self.repository.save(connection, updated)
        self._evidences(connection, row, [*baseline.evidences, *draft.evidences])
        self._append(connection, row, changes, payload.reason)
        return updated

    def _evidences(self, connection, row: dict, evidences: list) -> None:
        existing = self.repository.evidence_pairs(connection, row["id"])
        additions = []
        for evidence in evidences:
            pair = (evidence.field, evidence.value)
            if pair in existing:
                continue
            existing.add(pair)
            additions.append({
                "id": new_id("evd"), "event_id": None, "extraction_result_id": row["id"],
                "source_item_id": row["source_item_id"], "field_name": evidence.field,
                "field_value": evidence.value, "evidence_text": evidence.evidence,
                "confidence": None, "created_at": now_iso(),
            })
        self.extractions.create_evidences(additions, connection)

    def list(self, extraction_id: str, limit=50, offset=0) -> DraftCorrectionList:
        """分页读取历史；已处理草稿只读，保留其入库前的旧值和撤回关系。"""
        row, history = self.repository.read(extraction_id)
        current = json.loads(row["extracted_json"])
        items = []
        for item in history[offset:offset + limit]:
            group = [change for change in history if change["operation_id"] == item["operation_id"]]
            items.append(DraftCorrectionRead(
                **{key: value for key, value in item.items() if key not in {
                    "sequence", "old_value_json", "new_value_json",
                }},
                old_value=json.loads(item["old_value_json"]),
                new_value=json.loads(item["new_value_json"]),
                operation_field_count=len(group),
                can_retract=row["status"] == "pending_review" and all(
                    self._can_retract(change, history, current) for change in group
                ),
            ))
        return DraftCorrectionList(items=items, total=len(history), revision=len(history))

    @staticmethod
    def _can_retract(item, history, current) -> bool:
        return item["action"] == "correction" and (
            current.get(item["field_name"]) == json.loads(item["new_value_json"])
            and not any(other["field_name"] == item["field_name"]
                        and other["sequence"] > item["sequence"] for other in history)
        )

    def retract(self, extraction_id: str, operation_id: str, reason: str) -> ExtractionResultRead:
        """整次恢复旧候选并追加历史；缺年等旧问题可恢复为待核对，不能直接入库。"""
        with self.repository.edit(extraction_id) as (connection, row):
            if row["status"] != "pending_review":
                raise CorrectionConflict("草稿已处理，更正历史只读")
            history = self.repository.rows(connection, extraction_id)
            group = [item for item in history if item["operation_id"] == operation_id]
            if not group:
                raise KeyError(operation_id)
            if all(any(other["reverts_id"] == item["id"] for other in history) for item in group):
                return self.rules._to_read(row)
            original = json.loads(row["extracted_json"])
            if not all(self._can_retract(item, history, original) for item in group):
                raise CorrectionConflict("这次草稿更正已被后续修改覆盖，无法直接撤回")
            source = self.repository.source(connection, row["source_item_id"])
            changes = [(item["field_name"], json.loads(item["new_value_json"]),
                        json.loads(item["old_value_json"]), item["id"]) for item in group]
            draft = self.rules.prepare_review_draft(
                {**original, **{field: value for field, _, value, _ in changes}}, source,
            )
            updated = {**row, "extracted_json": dumps_json(draft.model_dump()),
                       "missing_fields": dumps_json(draft.missing_fields), "updated_at": now_iso(),
                       "revision": len(history) + len(changes)}
            self.repository.save(connection, updated)
            self._evidences(connection, row, draft.evidences)
            self._append(connection, row, changes, reason)
            return self.rules._to_read(updated)

    def reject(self, extraction_id: str) -> None:
        with self.repository.edit(extraction_id) as (connection, row):
            if row["status"] == "rejected":
                return
            if row["status"] != "pending_review":
                raise CorrectionConflict("草稿已确认，不能再作为草稿放弃")
            self.extractions.update_status(extraction_id, "rejected", now_iso(), connection)

    def _append(self, connection, row, changes, reason) -> None:
        operation_id, timestamp = new_id("draftop"), now_iso()
        for field, old, new, reverts_id in changes:
            self.repository.append(connection, {
                "id": new_id("draftchange"), "operation_id": operation_id,
                "extraction_result_id": row["id"], "field_name": field,
                "old_value_json": dumps_json(old), "new_value_json": dumps_json(new),
                "actor": "local_user_unverified", "reason": reason,
                "source_item_id": row["source_item_id"],
                "action": "retraction" if reverts_id else "correction",
                "reverts_id": reverts_id, "created_at": timestamp,
            })

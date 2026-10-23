from __future__ import annotations

from backend.app.core.evidence import FACT_FIELDS
from backend.app.core.ids import new_id
from backend.app.core.temporal import validate_event_times
from backend.app.core.time import now_iso
from backend.app.repositories.draft_repository import DraftRepository
from backend.app.repositories.event_repository import EventQuery, EventRepository
from backend.app.repositories.extraction_repository import (
    ExtractionRepository,
    dumps_json,
    loads_json,
)
from backend.app.schemas.event import (
    ACTIONABLE_STATUSES,
    STATUS_TRANSITIONS,
    USER_SETTABLE_STATUSES,
    EventConfirm,
    EventListResponse,
    EventRead,
    EventStatusUpdate,
    EventUpdate,
)
from backend.app.schemas.extraction import DraftEdit, EvidenceDraft, ExtractedEventDraft
from backend.app.services.correction_service import CorrectionConflict, CorrectionService
from backend.app.services.draft_service import DraftService


class EventStatusError(ValueError):
    """状态流转不合法。"""


class EventService:
    def __init__(
        self,
        event_repository: EventRepository | None = None,
        extraction_repository: ExtractionRepository | None = None,
    ) -> None:
        self.event_repository = event_repository or EventRepository()
        self.extraction_repository = extraction_repository or ExtractionRepository()

    # ---------- 从草稿确认入库 ----------

    def confirm_extraction(
        self, extraction_result_id: str, payload: EventConfirm
    ) -> EventRead:
        """锁定候选版本后确认；活动、来源关联、证据与草稿状态在同一事务提交。"""
        with DraftRepository().edit(extraction_result_id) as (connection, extraction):
            return self._confirm_locked(extraction_result_id, payload, connection, extraction)

    def _confirm_locked(self, extraction_result_id, payload, connection, extraction) -> EventRead:
        if extraction["status"] != "pending_review":
            raise ValueError("Extraction result is not pending review")
        if (payload.expected_revision is not None
                and payload.expected_revision != extraction["revision"]):
            raise CorrectionConflict("草稿已被更正，请重新读取后再确认")
        if payload.extracted is not None:
            # 兼容旧客户端的确认时修改，但元数据/证据不能由客户端自证。
            changes = {key: value for key, value in payload.extracted.model_dump().items()
                       if key in {*FACT_FIELDS, "schedule"}}
            extraction = DraftService().apply_locked(connection, extraction, DraftEdit(
                changes=changes, expected_revision=extraction["revision"],
            ))
        draft = ExtractedEventDraft.model_validate(
            loads_json(str(extraction["extracted_json"]), {})
        )
        validate_event_times(draft.model_dump())

        timestamp = now_iso()
        event_id = new_id("evt")
        row = self.event_repository.create(
            {
                "id": event_id,
                "title": draft.title,
                "event_type": draft.event_type,
                "organizer": draft.organizer,
                "registration_url": draft.registration_url,
                "registration_start_at": draft.registration_start_at,
                "registration_deadline_at": draft.registration_deadline_at,
                "event_start_at": draft.event_start_at,
                "event_end_at": draft.event_end_at,
                "location": draft.location,
                "is_online": int(draft.is_online) if draft.is_online is not None else None,
                "schedule_json": dumps_json(draft.schedule),
                "fee": draft.fee,
                "prize": draft.prize,
                "team_required": draft.team_required,
                "audience": draft.audience,
                "status": payload.status,
                "missing_fields": dumps_json(draft.missing_fields),
                "confidence": draft.confidence,
                "notes": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            source_item_id=str(extraction["source_item_id"]),
            connection=connection,
        )

        # 同一事务挂接原候选与更正后的证据；不信任确认请求自报的证据元数据。
        self.extraction_repository.attach_evidences_to_event(
            extraction_result_id, event_id, connection,
        )
        self._persist_evidences(
            extraction_result_id=extraction_result_id,
            source_item_id=str(extraction["source_item_id"]),
            event_id=event_id,
            evidences=draft.evidences,
            connection=connection,
        )
        self.extraction_repository.update_status(
            extraction_result_id, "confirmed", timestamp, connection,
        )

        return event_row_to_read(row)

    def _persist_evidences(
        self,
        *,
        extraction_result_id: str,
        source_item_id: str,
        event_id: str,
        evidences: list[EvidenceDraft],
        connection=None,
    ) -> None:
        """把草稿携带的 evidences 落库并关联到活动，按字段去重。"""
        if not evidences:
            return

        existing = self.extraction_repository.list_field_names(extraction_result_id, connection)
        rows: list[dict[str, object]] = []
        timestamp = now_iso()

        for evidence in evidences:
            # 抽取阶段已有同名字段证据时只补关联，不重复写入
            if evidence.field in existing:
                continue
            rows.append(
                {
                    "id": new_id("evd"),
                    "event_id": event_id,
                    "extraction_result_id": extraction_result_id,
                    "source_item_id": source_item_id,
                    "field_name": evidence.field,
                    "field_value": evidence.value,
                    "evidence_text": evidence.evidence,
                    "confidence": evidence.confidence,
                    "created_at": timestamp,
                }
            )

        self.extraction_repository.create_evidences(rows, connection)

    # ---------- 读取 ----------

    def list(self, query: EventQuery | None = None) -> list[EventRead]:
        rows = self.event_repository.query(query or EventQuery())
        return [event_row_to_read(row) for row in rows]

    def list_paginated(self, query: EventQuery | None = None) -> EventListResponse:
        query = query or EventQuery()
        items = self.list(query)
        return EventListResponse(
            items=items,
            total=self.event_repository.count(query),
            limit=query.limit,
            offset=query.offset,
        )

    def list_actionable(self) -> list[EventRead]:
        return [
            event_row_to_read(row)
            for row in self.event_repository.query(EventQuery(only_actionable=True))
        ]

    def get(self, event_id: str) -> EventRead | None:
        row = self.event_repository.get(event_id)
        return event_row_to_read(row) if row else None

    # ---------- 状态流转 ----------

    def update_status(
        self, event_id: str, payload: EventStatusUpdate, force: bool = False
    ) -> EventRead:
        row = self.event_repository.get(event_id)
        if row is None:
            raise KeyError(event_id)

        target = payload.status
        if target not in USER_SETTABLE_STATUSES:
            raise EventStatusError(
                f"不允许手动设置为 {target}，可选项：{'、'.join(USER_SETTABLE_STATUSES)}"
            )

        current = str(row["status"])
        if current == target:
            return event_row_to_read(row)

        allowed = STATUS_TRANSITIONS.get(current, set())
        if target not in allowed and not force:
            raise EventStatusError(
                f"不能从 {current} 直接变更为 {target}，"
                f"允许的目标状态：{'、'.join(sorted(allowed)) or '无'}"
            )

        updated = self.event_repository.update(
            event_id, {"status": target, "updated_at": now_iso()}
        )
        return event_row_to_read(updated)

    # ---------- 字段修正 ----------

    def update(self, event_id: str, payload: EventUpdate) -> EventRead:
        """人工编辑与更正历史同事务写入；状态迁移的统一规则由 T-004 继续处理。"""
        if "status" in payload.model_fields_set and payload.status not in USER_SETTABLE_STATUSES:
            raise EventStatusError("不允许手动设置该状态")
        return event_row_to_read(CorrectionService().update(event_id, payload))

    def delete(self, event_id: str) -> None:
        if self.event_repository.get(event_id) is None:
            raise KeyError(event_id)
        self.event_repository.delete(event_id)

    def get_or_raise(self, event_id: str) -> EventRead:
        row = self.event_repository.get(event_id)
        if row is None:
            raise KeyError(event_id)
        return event_row_to_read(row)

    def is_actionable(self, row: dict[str, object]) -> bool:
        return str(row.get("status")) in ACTIONABLE_STATUSES


# ---------- 共享转换 ----------

def event_row_to_read(row: dict[str, object]) -> EventRead:
    """events 表行 → EventRead。dedup / event service 共用，避免字段漂移。"""
    return EventRead(
        id=str(row["id"]),
        title=str(row["title"]),
        event_type=row.get("event_type"),  # type: ignore[arg-type]
        organizer=row.get("organizer"),  # type: ignore[arg-type]
        registration_url=row.get("registration_url"),  # type: ignore[arg-type]
        registration_start_at=row.get("registration_start_at"),  # type: ignore[arg-type]
        registration_deadline_at=row.get("registration_deadline_at"),  # type: ignore[arg-type]
        event_start_at=row.get("event_start_at"),  # type: ignore[arg-type]
        event_end_at=row.get("event_end_at"),  # type: ignore[arg-type]
        location=row.get("location"),  # type: ignore[arg-type]
        is_online=bool(row["is_online"]) if row.get("is_online") is not None else None,
        schedule=loads_json(row.get("schedule_json"), []),  # type: ignore[arg-type]
        fee=row.get("fee"),  # type: ignore[arg-type]
        prize=row.get("prize"),  # type: ignore[arg-type]
        team_required=row.get("team_required"),  # type: ignore[arg-type]
        audience=row.get("audience"),  # type: ignore[arg-type]
        status=str(row["status"]),
        missing_fields=loads_json(row.get("missing_fields"), []),  # type: ignore[arg-type]
        confidence=row.get("confidence"),  # type: ignore[arg-type]
        notes=row.get("notes"),  # type: ignore[arg-type]
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )

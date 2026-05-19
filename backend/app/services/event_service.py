from __future__ import annotations

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.event_repository import EventRepository
from backend.app.repositories.extraction_repository import (
    ExtractionRepository,
    dumps_json,
    loads_json,
)
from backend.app.schemas.event import EventConfirm, EventRead
from backend.app.schemas.extraction import ExtractedEventDraft
from backend.app.services.extraction_service import ExtractionService


class EventService:
    def __init__(
        self,
        event_repository: EventRepository | None = None,
        extraction_repository: ExtractionRepository | None = None,
        extraction_service: ExtractionService | None = None,
    ) -> None:
        self.event_repository = event_repository or EventRepository()
        self.extraction_repository = extraction_repository or ExtractionRepository()
        self.extraction_service = extraction_service or ExtractionService(
            extraction_repository=self.extraction_repository
        )

    def confirm_extraction(self, extraction_result_id: str, payload: EventConfirm) -> EventRead:
        extraction = self.extraction_repository.get(extraction_result_id)
        if extraction is None:
            raise ValueError("Extraction result not found")
        if extraction["status"] != "pending_review":
            raise ValueError("Extraction result is not pending review")

        draft = payload.extracted or ExtractedEventDraft.model_validate(
            loads_json(str(extraction["extracted_json"]), {})
        )
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
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            source_item_id=str(extraction["source_item_id"]),
        )
        self.extraction_repository.update_status(extraction_result_id, "confirmed", timestamp)
        return self._to_read(row)

    def list(self) -> list[EventRead]:
        return [self._to_read(row) for row in self.event_repository.list()]

    def list_actionable(self) -> list[EventRead]:
        return [self._to_read(row) for row in self.event_repository.list_actionable()]

    def get(self, event_id: str) -> EventRead | None:
        row = self.event_repository.get(event_id)
        return self._to_read(row) if row else None

    def _to_read(self, row: dict[str, object]) -> EventRead:
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
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

import re

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso, now_shanghai
from backend.app.repositories.extraction_repository import (
    ExtractionRepository,
    dumps_json,
    loads_json,
)
from backend.app.repositories.source_item_repository import SourceItemRepository
from backend.app.schemas.extraction import (
    EvidenceDraft,
    ExtractedEventDraft,
    ExtractionResultRead,
    ReviewQueueItem,
)


class ExtractionService:
    def __init__(
        self,
        extraction_repository: ExtractionRepository | None = None,
        source_repository: SourceItemRepository | None = None,
    ) -> None:
        self.extraction_repository = extraction_repository or ExtractionRepository()
        self.source_repository = source_repository or SourceItemRepository()

    def extract(self, source_item_id: str) -> ExtractionResultRead:
        source = self.source_repository.get(source_item_id)
        if source is None:
            raise ValueError("Source item not found")

        timestamp = now_iso()
        draft = self._fake_extract(source)
        result_id = new_id("ext")
        row = self.extraction_repository.create(
            {
                "id": result_id,
                "source_item_id": source_item_id,
                "status": "pending_review",
                "extracted_json": dumps_json(draft.model_dump()),
                "missing_fields": dumps_json(draft.missing_fields),
                "confidence": draft.confidence,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self.extraction_repository.create_evidences(
            [
                {
                    "id": new_id("evd"),
                    "event_id": None,
                    "extraction_result_id": result_id,
                    "source_item_id": source_item_id,
                    "field_name": evidence.field,
                    "field_value": evidence.value,
                    "evidence_text": evidence.evidence,
                    "confidence": evidence.confidence,
                    "created_at": timestamp,
                }
                for evidence in draft.evidences
            ]
        )
        self.source_repository.update_parse_status(source_item_id, "parsed", timestamp)
        return self._to_read(row)

    def list_review_queue(self) -> list[ReviewQueueItem]:
        return [self._to_review_item(row) for row in self.extraction_repository.list_pending()]

    def get(self, extraction_result_id: str) -> ExtractionResultRead | None:
        row = self.extraction_repository.get(extraction_result_id)
        return self._to_read(row) if row else None

    def reject(self, extraction_result_id: str) -> None:
        self.extraction_repository.update_status(extraction_result_id, "rejected", now_iso())

    def mark_confirmed(self, extraction_result_id: str) -> None:
        self.extraction_repository.update_status(extraction_result_id, "confirmed", now_iso())

    def _fake_extract(self, source: dict[str, object]) -> ExtractedEventDraft:
        text = str(source.get("raw_text") or "")
        url = str(source.get("source_url") or "") or None
        title = str(source.get("title") or "").strip() or self._guess_title(text, url)
        event_type = self._guess_event_type(text)
        deadline, deadline_evidence = self._guess_deadline(text)
        location = self._guess_location(text)
        is_online = self._guess_is_online(text)
        evidences: list[EvidenceDraft] = []

        if deadline:
            evidences.append(
                EvidenceDraft(
                    field="registration_deadline_at",
                    value=deadline,
                    evidence=deadline_evidence,
                    confidence=0.72,
                )
            )
        if url:
            evidences.append(
                EvidenceDraft(
                    field="registration_url",
                    value=url,
                    evidence="来源链接被暂用为报名/原文链接，需人工确认。",
                    confidence=0.35,
                )
            )

        missing_fields = [
            field
            for field, value in {
                "registration_deadline_at": deadline,
                "event_start_at": None,
                "location": location,
                "registration_url": url,
            }.items()
            if not value
        ]
        confidence = 0.45 + (0.15 if deadline else 0) + (0.1 if event_type else 0)

        return ExtractedEventDraft(
            title=title,
            event_type=event_type,
            registration_url=url,
            registration_deadline_at=deadline,
            location=location,
            is_online=is_online,
            missing_fields=missing_fields,
            confidence=min(confidence, 0.85),
            evidences=evidences,
        )

    def _guess_title(self, text: str, url: str | None) -> str:
        for line in text.splitlines():
            candidate = line.strip(" #\t")
            if 4 <= len(candidate) <= 80:
                return candidate
        return url or "未命名活动"

    def _guess_event_type(self, text: str) -> str | None:
        lowered = text.lower()
        if "黑客松" in text or "hackathon" in lowered:
            return "hackathon"
        if "比赛" in text or "大赛" in text:
            return "competition"
        if "沙龙" in text or "meetup" in lowered:
            return "salon"
        if "训练营" in text:
            return "bootcamp"
        if "会议" in text or "大会" in text:
            return "conference"
        return None

    def _guess_deadline(self, text: str) -> tuple[str | None, str | None]:
        pattern = re.compile(
            r"(报名截止|截止报名|截止时间|报名时间).*?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?"
            r"(?:\s*(\d{1,2})[:：](\d{2}))?",
            re.S,
        )
        match = pattern.search(text)
        if not match:
            return None, None

        year = now_shanghai().year
        month = int(match.group(2))
        day = int(match.group(3))
        hour = int(match.group(4) or 23)
        minute = int(match.group(5) or 59)
        value = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00+08:00"
        return value, match.group(0)[:160]

    def _guess_location(self, text: str) -> str | None:
        for city in ["北京", "上海", "深圳", "广州", "杭州", "成都", "南京", "武汉", "苏州"]:
            if city in text:
                return city
        if self._guess_is_online(text):
            return "线上"
        return None

    def _guess_is_online(self, text: str) -> bool | None:
        if any(keyword in text for keyword in ["线上", "直播", "腾讯会议", "飞书会议"]):
            return True
        if any(keyword in text for keyword in ["线下", "现场", "路演"]):
            return False
        return None

    def _to_read(self, row: dict[str, object]) -> ExtractionResultRead:
        extracted = ExtractedEventDraft.model_validate(loads_json(str(row["extracted_json"]), {}))
        return ExtractionResultRead(
            id=str(row["id"]),
            source_item_id=str(row["source_item_id"]),
            status=row["status"],  # type: ignore[arg-type]
            extracted=extracted,
            missing_fields=list(loads_json(row.get("missing_fields"), [])),  # type: ignore[arg-type]
            confidence=row.get("confidence"),  # type: ignore[arg-type]
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _to_review_item(self, row: dict[str, object]) -> ReviewQueueItem:
        return ReviewQueueItem(
            extraction=self._to_read(row),
            source_title=row.get("source_title"),  # type: ignore[arg-type]
            source_url=row.get("source_url"),  # type: ignore[arg-type]
            source_text=row.get("source_text"),  # type: ignore[arg-type]
            source_type=str(row["source_type"]),
        )


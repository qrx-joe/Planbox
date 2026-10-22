from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from backend.app.core.ids import new_id
from backend.app.core.temporal import TimeValueError, parse_time
from backend.app.core.time import now_iso
from backend.app.providers.llm import BaseLLMProvider, FakeLLMProvider, OpenAIProvider
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

if TYPE_CHECKING:
    from backend.app.core.config import Settings


class ExtractionService:
    def __init__(
        self,
        extraction_repository: ExtractionRepository | None = None,
        source_repository: SourceItemRepository | None = None,
        llm_provider: BaseLLMProvider | None = None,
    ) -> None:
        self.extraction_repository = extraction_repository or ExtractionRepository()
        self.source_repository = source_repository or SourceItemRepository()
        self.llm_provider = llm_provider

    def _get_llm_provider(self) -> BaseLLMProvider:
        if self.llm_provider is not None:
            return self.llm_provider
        from backend.app.core.config import settings

        if settings.llm_provider == "openai" and settings.openai_api_key:
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                model=settings.openai_model,
            )
        return FakeLLMProvider()

    async def extract(self, source_item_id: str) -> ExtractionResultRead:
        source = self.source_repository.get(source_item_id)
        if source is None:
            raise ValueError("Source item not found")

        timestamp = now_iso()
        provider = self._get_llm_provider()
        text = str(source.get("raw_text") or "")
        url = str(source.get("source_url") or "") or None

        if isinstance(provider, FakeLLMProvider):
            draft = self._rule_based_extract(source)
        else:
            draft = await self._llm_extract(provider, text, url)

        # 参照来自实际来源记录；仍不自动推断跨年或相对日期。
        for reference_kind in ("published_at", "captured_at"):
            reference = source.get(reference_kind)
            try:
                parse_time(reference)
            except TimeValueError:
                continue
            draft.time_issues = [replace(
                issue, reference_at=reference, reference_kind=reference_kind
            ) for issue in draft.time_issues]
            break

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

    async def _llm_extract(
        self,
        provider: BaseLLMProvider,
        text: str,
        url: str | None,
    ) -> ExtractedEventDraft:
        raw_result = await provider.extract_event(text, url)

        evidences = self._generate_evidences(raw_result, text)

        missing_fields = self._compute_missing_fields(raw_result)

        return ExtractedEventDraft(
            title=raw_result.title,
            event_type=raw_result.event_type,
            organizer=raw_result.organizer,
            registration_url=raw_result.registration_url,
            registration_start_at=raw_result.registration_start_at,
            registration_deadline_at=raw_result.registration_deadline_at,
            event_start_at=raw_result.event_start_at,
            event_end_at=raw_result.event_end_at,
            location=raw_result.location,
            is_online=raw_result.is_online,
            schedule=raw_result.schedule,
            fee=raw_result.fee,
            prize=raw_result.prize,
            team_required=raw_result.team_required,
            audience=raw_result.audience,
            missing_fields=missing_fields,
            confidence=raw_result.confidence,
            evidences=evidences,
        )

    def _generate_evidences(
        self,
        draft: ExtractedEventDraft,
        source_text: str,
    ) -> list[EvidenceDraft]:
        evidences: list[EvidenceDraft] = []

        field_mapping = {
            "title": draft.title,
            "event_type": draft.event_type,
            "organizer": draft.organizer,
            "registration_url": draft.registration_url,
            "registration_deadline_at": draft.registration_deadline_at,
            "event_start_at": draft.event_start_at,
            "location": draft.location,
            "fee": draft.fee,
        }

        for field_name, field_value in field_mapping.items():
            if field_value:
                evidence_text = self._find_evidence_text(field_value, source_text)
                evidences.append(
                    EvidenceDraft(
                        field=field_name,
                        value=str(field_value),
                        evidence=evidence_text,
                        confidence=0.8,
                    )
                )

        return evidences

    def _find_evidence_text(self, value: str, source_text: str) -> str:
        max_len = 200
        if len(source_text) <= max_len:
            return source_text

        idx = source_text.find(str(value))
        if idx == -1:
            return source_text[:max_len]

        start = max(0, idx - 50)
        end = min(len(source_text), idx + len(str(value)) + 50)
        return source_text[start:end]

    def _compute_missing_fields(self, draft: ExtractedEventDraft) -> list[str]:
        required_fields = [
            "registration_deadline_at",
            "event_start_at",
            "location",
            "registration_url",
        ]
        missing = []
        for field in required_fields:
            if getattr(draft, field, None) is None:
                missing.append(field)
        return missing

    def list_review_queue(self) -> list[ReviewQueueItem]:
        return [self._to_review_item(row) for row in self.extraction_repository.list_pending()]

    def get(self, extraction_result_id: str) -> ExtractionResultRead | None:
        row = self.extraction_repository.get(extraction_result_id)
        return self._to_read(row) if row else None

    def reject(self, extraction_result_id: str) -> None:
        self.extraction_repository.update_status(extraction_result_id, "rejected", now_iso())

    def mark_confirmed(self, extraction_result_id: str) -> None:
        self.extraction_repository.update_status(extraction_result_id, "confirmed", now_iso())

    def _rule_based_extract(self, source: dict[str, object]) -> ExtractedEventDraft:
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
        """仅提取明确截止片段；缺年/时区保留原值，日期不补截止时刻。"""
        pattern = re.compile(
            r"(?:报名截止|截止报名|截止时间)\s*[:：]?\s*"
            r"(?P<day>(?:(?P<year>\d{4})[年./-])?"
            r"(?P<month>\d{1,2})[月./-](?P<date>\d{1,2})[日号]?|今天|明天|后天|下周[一二三四五六日天])"
            r"(?:[Tt\s]*(?P<hour>\d{1,2})[:：](?P<minute>\d{2})"
            r"(?::(?P<second>\d{2}))?(?P<offset>Z|[+-]\d{2}:\d{2})?)?"
        )
        match = pattern.search(text)
        if not match:
            return None, None

        if not match.group("year"):
            return text[match.start("day"):match.end()], match.group(0)[:160]
        value = (
            f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-"
            f"{int(match.group('date')):02d}"
        )
        if match.group("hour") is not None:
            value += (
                f"T{int(match.group('hour')):02d}:{int(match.group('minute')):02d}:"
                f"{int(match.group('second') or 0):02d}{match.group('offset') or ''}"
            )
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

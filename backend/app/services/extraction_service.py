from __future__ import annotations

import re
from asyncio import CancelledError
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.core.config import settings
from backend.app.core.evidence import FACT_FIELDS, locate_evidence
from backend.app.core.ids import new_id
from backend.app.core.temporal import TimeValueError, parse_time
from backend.app.core.time import now_iso, now_shanghai
from backend.app.providers.llm import BaseLLMProvider, FakeLLMProvider, OpenAIProvider
from backend.app.repositories.extraction_repository import (
    ExtractionRepository,
    dumps_json,
    loads_json,
)
from backend.app.repositories.source_item_repository import (
    SourceItemRepository,
    SourceStateConflict,
)
from backend.app.schemas.extraction import (
    EvidenceDraft,
    ExtractedEventDraft,
    ExtractionResultRead,
    ReviewQueueItem,
)


class ExtractionFailed(RuntimeError):
    """模型抽取失败；对外仅提供固定提示，不泄漏上游异常或原文。"""


EXTRACTION_ERROR = "抽取失败，请重试；若持续失败请检查模型配置或服务。"


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

    def prepare_review_draft(
        self, data: dict[str, object], source: dict[str, object],
    ) -> ExtractedEventDraft:
        """更正后重新标记缺失/时间问题并定位证据；保留自定义未知项，不补猜日期。

        原始证据由调用方保留在证据表。重建只产生当前候选，无数据库/模型副作用。
        """
        draft = ExtractedEventDraft.model_validate(data)
        editable = {*FACT_FIELDS, "schedule"}
        custom = [name for name in draft.missing_fields
                  if name not in editable and not name.startswith("schedule.")]
        draft.missing_fields = list(dict.fromkeys([*custom, *self._compute_missing_fields(draft)]))
        draft = ExtractedEventDraft.model_validate(draft.model_dump())
        draft.evidences = self._generate_evidences(draft, str(source.get("raw_text") or ""))
        draft.time_issues = [replace(issue, reference_at=None, reference_kind=None)
                             for issue in draft.time_issues]
        for kind in ("published_at", "captured_at"):
            reference = source.get(kind)
            try:
                parse_time(reference)
            except TimeValueError:
                continue
            draft.time_issues = [replace(issue, reference_at=reference, reference_kind=kind)
                                 for issue in draft.time_issues]
            break
        return draft

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

    # 认领后超过该时限仍停在 pending，判定为进程崩溃遗留，允许重新认领；
    # 阈值远大于模型调用超时，正常在途请求不会被二次认领。
    STALE_PENDING_AFTER = timedelta(minutes=15)

    def _reject_in_flight_or_stale(self, source: dict[str, object]) -> None:
        """宽限期内视为在途抽取；认领时间解析失败时保守拒绝，不误接管。

        接管后旧事务即使恢复，写回前会重读状态并因不是 pending 而中止，
        不会覆盖新一次抽取的结果。
        """
        claimed = None
        try:
            claimed = datetime.fromisoformat(str(source["updated_at"]))
        except ValueError:
            claimed = None
        if claimed is not None and claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=ZoneInfo(settings.timezone))
        if claimed is None or now_shanghai() - claimed < self.STALE_PENDING_AFTER:
            raise SourceStateConflict(
                "来源正在抽取，请稍后重试；长时间无结果可稍后再试或核对状态"
            )

    async def extract(self, source_item_id: str) -> ExtractionResultRead:
        """抽取来源文本；成功原子保存候选/证据并标 parsed，失败标 failed 后抛出。

        unparsed/failed 可尝试，parsed 复用既有结果（保留核对状态与更正）；
        pending 在宽限期内抛 SourceStateConflict，超过 STALE_PENDING_AFTER 视为
        上次进程崩溃遗留、允许重新认领；其余异常状态同样抛 SourceStateConflict。
        缺来源抛 KeyError，缺正文抛 ValueError。模型失败抛 ExtractionFailed；
        持久化错误回滚并原样抛出。短事务认领阻止独立请求重复调用，网络期间不持锁。
        """
        with self.source_repository.edit(source_item_id) as (connection, source):
            state = source["parse_status"]
            if state == "parsed":
                previous = self.extraction_repository.latest_for_source(source_item_id, connection)
                if previous is None:
                    raise SourceStateConflict("来源已标记解析成功但缺少结果，请先核对数据")
                return self._to_read(previous)
            if state == "pending":
                self._reject_in_flight_or_stale(source)
            elif state not in {"unparsed", "failed"}:
                raise SourceStateConflict("来源解析状态异常，请核对状态")
            text = str(source.get("raw_text") or "")
            if source.get("ingest_state") == "needs_content" or not text.strip():
                raise ValueError("请先补全正文；当前抽取只支持文字，不能直接读取链接或截图")
            self.source_repository.update_parse_status(
                source_item_id, "pending", now_iso(), connection=connection,
            )

        try:
            try:
                provider = self._get_llm_provider()
                url = str(source.get("source_url") or "") or None
                if isinstance(provider, FakeLLMProvider):
                    draft = self._rule_based_extract(source)
                else:
                    draft = await self._llm_extract(provider, text, url)
            except Exception as exc:
                raise ExtractionFailed(EXTRACTION_ERROR) from exc

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

            timestamp = now_iso()
            result_id = new_id("ext")
            with self.source_repository.edit(source_item_id) as (connection, current):
                if current["parse_status"] != "pending":
                    raise SourceStateConflict("抽取状态已改变，请重新读取来源状态")
                row = self.extraction_repository.create({
                    "id": result_id,
                    "source_item_id": source_item_id,
                    "status": "pending_review",
                    "extracted_json": dumps_json(draft.model_dump()),
                    "missing_fields": dumps_json(draft.missing_fields),
                    "confidence": draft.confidence,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }, connection)
                self.extraction_repository.create_evidences([
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
                ], connection)
                self.source_repository.update_parse_status(
                    source_item_id, "parsed", timestamp, connection=connection,
                )
                result = self._to_read(row)
            return result
        except (Exception, CancelledError):
            with self.source_repository.edit(source_item_id) as (connection, current):
                if current["parse_status"] == "pending":
                    self.source_repository.update_parse_status(
                        source_item_id, "failed", now_iso(), EXTRACTION_ERROR, connection,
                    )
            raise

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
            "registration_start_at": draft.registration_start_at,
            "registration_deadline_at": draft.registration_deadline_at,
            "event_start_at": draft.event_start_at,
            "event_end_at": draft.event_end_at,
            "location": draft.location,
            "is_online": draft.is_online,
            "fee": draft.fee,
            "prize": draft.prize,
            "team_required": draft.team_required,
            "audience": draft.audience,
        }
        for index, node in enumerate(draft.schedule):
            for key in ("name", "start_at", "end_at"):
                field_mapping[f"schedule.{index}.{key}"] = node.get(key)

        for field_name, field_value in field_mapping.items():
            if field_value is not None and field_value != "":
                located = locate_evidence(str(field_value), source_text)
                evidences.append(
                    EvidenceDraft(
                        field=field_name,
                        value=str(field_value),
                        evidence=located.quote if located else None,
                        confidence=None,
                        verification_status="located" if located else "unverified",
                        start_offset=located.start_offset if located else None,
                        end_offset=located.end_offset if located else None,
                    )
                )

        return evidences

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

    def mark_confirmed(self, extraction_result_id: str) -> None:
        self.extraction_repository.update_status(extraction_result_id, "confirmed", now_iso())

    def _rule_based_extract(self, source: dict[str, object]) -> ExtractedEventDraft:
        text = str(source.get("raw_text") or "")
        url = str(source.get("source_url") or "") or None
        title = str(source.get("title") or "").strip() or self._guess_title(text, url)
        event_type = self._guess_event_type(text)
        deadline, _ = self._guess_deadline(text)
        location = self._guess_location(text)
        is_online = self._guess_is_online(text)

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

        draft = ExtractedEventDraft(
            title=title,
            event_type=event_type,
            registration_url=url,
            registration_deadline_at=deadline,
            location=location,
            is_online=is_online,
            missing_fields=missing_fields,
            confidence=min(confidence, 0.85),
        )
        draft.evidences = self._generate_evidences(draft, text)
        return draft

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
            revision=int(row.get("revision", 0)),
        )

    def _to_review_item(self, row: dict[str, object]) -> ReviewQueueItem:
        return ReviewQueueItem(
            extraction=self._to_read(row),
            source_title=row.get("source_title"),  # type: ignore[arg-type]
            source_url=row.get("source_url"),  # type: ignore[arg-type]
            source_text=row.get("source_text"),  # type: ignore[arg-type]
            source_type=str(row["source_type"]),
        )

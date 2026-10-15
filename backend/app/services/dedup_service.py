"""活动去重与合并。

匹配策略按可信度从高到低：
1. 报名链接完全一致 —— 几乎可以确定是同一个活动
2. 归一化后活动名完全一致
3. 活动名高度相似（字符集相似度）+ 举办时间接近
4. 主办方 + 举办时间一致

只做提示与辅助合并，不自动删除任何数据。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from backend.app.core.time import now_iso
from backend.app.repositories.event_repository import EventQuery, EventRepository
from backend.app.repositories.evidence_repository import EvidenceRepository
from backend.app.schemas.event import (
    DuplicateCandidate,
    DuplicateCheckResponse,
    EventMergeResponse,
)
from backend.app.services.event_service import event_row_to_read

# 归一化时剔除的噪声词：括注、序号、赛季标记等
NOISE_PATTERN = re.compile(
    r"[（(\[【][^）)\]】]*[）)\]】]"  # 中文/英文括注
    r"|第[一二三四五六七八九十百\d]+[届期]"
    r"|20\d{2}[年\-/\.]\d{1,2}[月\-/\.]\d{0,2}日?"  # 日期后缀
    r"|[\s\-_·•|,，。!！?？]+"  # 分隔符与标点
)

# 候选集上限：活动数量大时只取最近 N 条做比对，避免全表两两比较
CANDIDATE_POOL_SIZE = 200

# 相似度阈值（0-1）
SIMILARITY_THRESHOLD = 0.72

# 时间接近阈值（小时）
START_TIME_TOLERANCE_HOURS = 48


class DedupService:
    def __init__(
        self,
        event_repository: EventRepository | None = None,
        evidence_repository: EvidenceRepository | None = None,
    ) -> None:
        self.event_repository = event_repository or EventRepository()
        self.evidence_repository = evidence_repository or EvidenceRepository()

    # ---------- 对外接口 ----------

    def find_duplicates(
        self, event_id: str, threshold: float = SIMILARITY_THRESHOLD
    ) -> DuplicateCheckResponse:
        target = self.event_repository.get(event_id)
        if target is None:
            return DuplicateCheckResponse(candidates=[], has_duplicates=False)

        candidates = self._score_candidates(target, threshold)
        return DuplicateCheckResponse(candidates=candidates, has_duplicates=bool(candidates))

    def check_draft(self, draft: dict[str, object]) -> DuplicateCheckResponse:
        """对尚未入库的抽取草稿做重复预检。

        让用户在 confirm 之前就能看到「这个活动库里已经有了」，
        避免先入库再去手动清理重复卡片。
        """
        if not draft.get("title"):
            return DuplicateCheckResponse(candidates=[], has_duplicates=False)

        probe = self._score_candidates(draft, SIMILARITY_THRESHOLD)
        return DuplicateCheckResponse(candidates=probe, has_duplicates=bool(probe))

    def merge(self, target_id: str, source_id: str) -> EventMergeResponse:
        target = self.event_repository.get(target_id)
        source = self.event_repository.get(source_id)
        if target is None or source is None:
            raise KeyError("event")
        if target_id == source_id:
            raise ValueError("Cannot merge an event into itself")

        # 补全：target 为空、source 有值时，用 source 的值填补
        filled_fields: list[str] = []
        updates: dict[str, object] = {}
        for column in (
            "event_type",
            "organizer",
            "registration_url",
            "registration_start_at",
            "registration_deadline_at",
            "event_start_at",
            "event_end_at",
            "location",
            "is_online",
            "fee",
            "prize",
            "team_required",
            "audience",
            "notes",
        ):
            if not target.get(column) and source.get(column):
                updates[column] = source[column]
                filled_fields.append(column)

        # 赛程合并：按名称去重后拼接，保留 target 原有顺序
        target_schedule = self._parse_schedule(target.get("schedule_json"))
        merged_schedule = self._merge_schedule(
            target_schedule, self._parse_schedule(source.get("schedule_json"))
        )
        if merged_schedule != target_schedule:
            updates["schedule_json"] = json.dumps(merged_schedule, ensure_ascii=False)
            filled_fields.append("schedule")

        # 证据补全：target 缺失或 source 更可信时采纳 source 的证据
        merged_confidence = self._backfill_evidences(target_id, source_id)
        target_confidence = float(target.get("confidence") or 0.0)
        if merged_confidence is not None and merged_confidence > target_confidence:
            updates["confidence"] = merged_confidence
            filled_fields.append("confidence")

        if updates:
            self.event_repository.update(target_id, updates)

        transferred = self._transfer_sources(source_id, target_id)

        # 状态取两者中更「可行动」的一个
        status = self._merge_status(str(target.get("status")), str(source.get("status")))
        if status != target.get("status"):
            self.event_repository.update(target_id, {"status": status})
            filled_fields.append("status")

        merged_row = self.event_repository.get_or_raise(target_id)
        self.event_repository.delete(source_id)

        return EventMergeResponse(
            merged=event_row_to_read(merged_row),
            transferred_sources=transferred,
            filled_fields=filled_fields,
            deleted_event_id=source_id,
        )

    # ---------- 匹配实现 ----------

    def _score_candidates(
        self, target: dict[str, object], threshold: float
    ) -> list[DuplicateCandidate]:
        # 草稿尚未入库，没有 id，用空串代表「排除自身」这一步可跳过
        target_id = str(target.get("id") or "")
        # 用target 的关键字段缩小候选范围，而不是全表扫描
        probe = EventQuery(
            keyword=str(target.get("title") or "")[:8] or None,
            event_type=str(target.get("event_type")) if target.get("event_type") else None,
            limit=CANDIDATE_POOL_SIZE,
        )
        pool = self.event_repository.query(probe)
        if target_id:
            pool = [row for row in pool if str(row["id"]) != target_id]

        # 关键词查询可能漏掉标题差异大的重复项，用全量池兜底
        if len(pool) < CANDIDATE_POOL_SIZE:
            for row in self.event_repository.query(EventQuery(limit=CANDIDATE_POOL_SIZE)):
                if str(row["id"]) == target_id:
                    continue
                if not any(str(existing["id"]) == str(row["id"]) for existing in pool):
                    pool.append(row)

        candidates: list[DuplicateCandidate] = []
        for row in pool:
            reasons, score = self._compare(target, row)
            if score >= threshold and reasons:
                candidates.append(
                    DuplicateCandidate(
                        event=event_row_to_read(row),
                        match_reasons=reasons,
                        match_score=round(score, 3),
                    )
                )

        candidates.sort(key=lambda item: item.match_score, reverse=True)
        return candidates

    def _compare(
        self, left: dict[str, object], right: dict[str, object]
    ) -> tuple[list[str], float]:
        reasons: list[str] = []
        score = 0.0

        # 1. 报名链接完全一致 —— 最强信号
        left_url = _normalize_url(left.get("registration_url"))
        right_url = _normalize_url(right.get("registration_url"))
        if left_url and right_url and left_url == right_url:
            reasons.append("报名链接完全一致")
            return reasons, 1.0

        # 2/3. 名称比对
        left_title = str(left.get("title") or "")
        right_title = str(right.get("title") or "")
        left_norm = _normalize_title(left_title)
        right_norm = _normalize_title(right_title)

        if left_norm and right_norm and left_norm == right_norm:
            reasons.append("活动名称一致（忽略括注与赛季标记）")
            name_score = 0.9
        else:
            similarity = _similarity(left_norm, right_norm)
            name_score = similarity
            if similarity >= SIMILARITY_THRESHOLD:
                reasons.append(f"活动名称相似（{similarity:.0%}）")

        # 举办时间接近
        same_time, time_bonus = _time_proximity(
            left.get("event_start_at"), right.get("event_start_at")
        )
        if same_time:
            reasons.append("举办时间接近")

        # 主办方一致
        left_org = str(left.get("organizer") or "").strip()
        right_org = str(right.get("organizer") or "").strip()
        same_org = bool(left_org and right_org and left_org == right_org)
        if same_org:
            reasons.append("主办方一致")

        score = name_score + time_bonus + (0.1 if same_org else 0.0)
        # 时间或主办方任一命中时，适度放宽名称要求
        if (same_time or same_org) and name_score >= 0.5:
            score = max(score, 0.75)
        else:
            score = min(score, name_score + 0.2)

        return reasons, score

    # ---------- 合并辅助 ----------

    def _merge_status(self, target_status: str, source_status: str) -> str:
        actionable = {"interested", "applied"}
        if source_status in actionable and target_status not in actionable:
            return source_status
        return target_status

    def _parse_schedule(self, raw: object) -> list[dict[str, str | None]]:
        import json

        if not raw:
            return []
        try:
            value = json.loads(str(raw))
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    def _merge_schedule(
        self,
        target_schedule: list[dict[str, str | None]],
        source_schedule: list[dict[str, str | None]],
    ) -> list[dict[str, str | None]]:
        merged = list(target_schedule)
        seen = {_normalize_title(str(item.get("name") or "")) for item in target_schedule}
        for item in source_schedule:
            key = _normalize_title(str(item.get("name") or ""))
            if key and key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    def _transfer_sources(self, source_id: str, target_id: str) -> int:
        """把 source 的来源关联迁移到 target，保持多来源可溯源（PRD 8.9）。

        同时把该活动下的证据改挂到 target，source 随后被删除，证据不丢失。
        """
        from backend.app.db.database import connect

        with connect() as connection:
            rows = connection.execute(
                "SELECT source_item_id FROM event_sources WHERE event_id = ?",
                (source_id,),
            ).fetchall()
            timestamp = now_iso()
            for row in rows:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO event_sources (event_id, source_item_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (target_id, row["source_item_id"], timestamp),
                )
            connection.execute(
                "UPDATE evidences SET event_id = ? WHERE event_id = ?",
                (target_id, source_id),
            )
            connection.commit()
            return len(rows)

    def _backfill_evidences(self, target_id: str, source_id: str) -> float | None:
        """用 source 的证据补全 target，返回合并后的活动置信度。

        规则：某字段在 target 缺失、或 source 置信度更高时，才采纳 source 的证据。
        活动整体置信度取两侧全部证据的最大值。
        """
        target_evidences = {
            str(item["field_name"]): item
            for item in self.evidence_repository.list_by_event(target_id)
        }
        source_evidences = {
            str(item["field_name"]): item
            for item in self.evidence_repository.list_by_event(source_id)
        }

        for field_name, source_evidence in source_evidences.items():
            source_confidence = float(source_evidence.get("confidence") or 0.0)
            existing = target_evidences.get(field_name)
            existing_confidence = float(existing.get("confidence") or 0.0) if existing else 0.0
            if source_confidence > existing_confidence:
                target_evidences[field_name] = source_evidence

        confidences = [
            float(item["confidence"])
            for item in target_evidences.values()
            if item.get("confidence") is not None
        ]
        return max(confidences) if confidences else None


# ---------- 纯函数工具 ----------

def _normalize_title(title: str) -> str:
    if not title:
        return ""
    lowered = title.strip().lower()
    return NOISE_PATTERN.sub("", lowered)


def _normalize_url(url: object) -> str:
    if not url:
        return ""
    text = str(url).strip().lower()
    text = re.sub(r"[?#].*$", "", text)
    return text.rstrip("/")


def _similarity(left: str, right: str) -> float:
    """基于字符二元组的 Dice 系数，对中文短标题比编辑距离更稳。"""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if len(left) < 2 or len(right) < 2:
        return 1.0 if left == right else 0.0

    left_grams = {left[i : i + 2] for i in range(len(left) - 1)}
    right_grams = {right[i : i + 2] for i in range(len(right) - 1)}
    if not left_grams or not right_grams:
        return 0.0

    overlap = len(left_grams & right_grams)
    return (2.0 * overlap) / (len(left_grams) + len(right_grams))


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _time_proximity(left: object, right: object) -> tuple[bool, float]:
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None or right_dt is None:
        return False, 0.0

    delta_hours = abs((left_dt - right_dt).total_seconds()) / 3600.0
    if delta_hours <= START_TIME_TOLERANCE_HOURS:
        return True, 0.15
    if delta_hours <= 24 * 7:
        return False, 0.05
    return False, 0.0
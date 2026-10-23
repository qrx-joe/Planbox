"""正文位置匹配；找到片段只表示可定位，不表示字段含义已经被核实。"""

import re
from dataclasses import dataclass
from datetime import date

FACT_FIELDS = (
    "title", "event_type", "organizer", "registration_url", "registration_start_at",
    "registration_deadline_at", "event_start_at", "event_end_at", "location", "is_online",
    "fee", "prize", "team_required", "audience",
)


@dataclass(frozen=True)
class LocatedEvidence:
    quote: str
    start_offset: int
    end_offset: int


def locate_evidence(value: str, source_text: str) -> LocatedEvidence | None:
    """返回原文中的连续片段和 Python Unicode 字符偏移（左闭右开），找不到返回 None。

    先做完整字面匹配；仅对 date 值允许完整年月日的等价书写。
    不使用日期片段去证明额外时刻/时区，不推断近义词、主办方关系或图片位置。
    """
    if not value:
        return None
    start = source_text.find(value)
    end = start + len(value)
    if start < 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        pattern = re.compile(
            rf"(?<!\d){day.year:04d}[年/-](\d{{1,2}})[月/-](\d{{1,2}})(?!\d)[日号]?"
        )
        for match in pattern.finditer(source_text):
            if int(match.group(1)) == day.month and int(match.group(2)) == day.day:
                start, end = match.span()
                break
    if start < 0:
        return None
    start, end = max(0, start - 50), min(len(source_text), end + 50)
    return LocatedEvidence(source_text[start:end], start, end)

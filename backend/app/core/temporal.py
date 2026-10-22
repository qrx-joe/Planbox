"""活动时间的纯规则：保留日期精度，拒绝猜测年份、时区和结束时刻。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

TIME_FIELDS = (
    "registration_start_at",
    "registration_deadline_at",
    "event_start_at",
    "event_end_at",
)
TimeCode = Literal["invalid", "missing_timezone", "incomplete", "mixed_precision", "range_order"]
MESSAGES = {
    "invalid": "日期或时间无效",
    "missing_timezone": "时区未确认，请提供明确偏移",
    "incomplete": "年份或相对时间未确认",
    "mixed_precision": "区间两端的日期/时刻精度需一致",
    "range_order": "结束应晚于开始（日期区间允许同日）",
}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:[Zz]|[+-]\d{2}:\d{2})?"
)


@dataclass(frozen=True)
class TimeIssue:
    """待核对字段；raw_value 保留原值，参照不表示相对时间已经确定。"""

    field: str
    code: TimeCode
    raw_value: str | None
    reference_at: str | None = None
    reference_kind: Literal["published_at", "captured_at"] | None = None


class TimeValueError(ValueError):
    def __init__(self, code: TimeCode):
        self.code = code
        super().__init__(MESSAGES[code])


class TimeValidationError(ValueError):
    """用于新写入/查询/导出边界；错误消息不包含用户原始值。"""

    def __init__(self, issues: list[TimeIssue]):
        self.issues = issues
        labels = {
            "registration_start_at": "报名开始", "registration_deadline_at": "报名截止",
            "event_start_at": "活动开始", "event_end_at": "活动结束",
        }
        super().__init__("；".join(
            f"{labels.get(issue.field, '赛程时间')}：{MESSAGES[issue.code]}" for issue in issues
        ))


def parse_time(value: str) -> date | datetime:
    """解析 ISO 日期或明确偏移的扩展时间，无副作用；不支持的值抛 TimeValueError。

    返回 date 表示只有日期；datetime 必须带明确时区，-00:00 按偏移未知处理。
    秒可省略，小数秒最多六位；UTC 转换必须在 Python 可表达范围内。
    """
    if not isinstance(value, str) or not value:
        raise TimeValueError("invalid")
    try:
        if _DATE.fullmatch(value):
            return date.fromisoformat(value)
        if _DATETIME.fullmatch(value):
            offset = re.search(r"[+-](\d{2}):(\d{2})$", value)
            if offset and (int(offset.group(1)) > 23 or int(offset.group(2)) > 59):
                raise TimeValueError("invalid")
            parsed = datetime.fromisoformat(value.replace("z", "+00:00").replace("Z", "+00:00"))
            if parsed.tzinfo is None or value.endswith("-00:00"):
                raise TimeValueError("missing_timezone")
            parsed.astimezone(UTC)
            return parsed
    except TimeValueError:
        raise
    except (ValueError, OverflowError) as exc:
        raise TimeValueError("invalid") from exc
    raise TimeValueError("invalid" if re.match(r"\d{4}[-年]", value) else "incomplete")


def inspect_event_times(values: Mapping[str, Any]) -> list[TimeIssue]:
    """检查活动与赛程的值和区间；未知 null 允许存在，不修改输入或读取存储。"""
    issues: list[TimeIssue] = []

    def inspect(field: str, value: Any) -> date | datetime | None:
        if value is None:
            return None
        try:
            return parse_time(value)
        except TimeValueError as exc:
            issues.append(TimeIssue(field, exc.code, str(value)))
            return None

    def pair(start: Any, end: Any, field: str, raw: Any) -> None:
        if start is None or end is None:
            return
        if type(start) is not type(end):
            issues.append(TimeIssue(field, "mixed_precision", raw))
        elif end < start or (isinstance(start, datetime) and end == start):
            issues.append(TimeIssue(field, "range_order", raw))

    parsed = {field: inspect(field, values.get(field)) for field in TIME_FIELDS}
    for start_field, end_field in (
        ("registration_start_at", "registration_deadline_at"),
        ("event_start_at", "event_end_at"),
    ):
        pair(parsed[start_field], parsed[end_field], end_field, values.get(end_field))
    for index, node in enumerate(values.get("schedule") or []):
        if not isinstance(node, dict):
            issues.append(TimeIssue(f"schedule.{index}", "invalid", None))
            continue
        start = inspect(f"schedule.{index}.start_at", node.get("start_at"))
        end = inspect(f"schedule.{index}.end_at", node.get("end_at"))
        pair(start, end, f"schedule.{index}.end_at", node.get("end_at"))
    return issues


def validate_event_times(values: Mapping[str, Any]) -> None:
    """新确认或时间修改前检查完整候选值；有问题时拒绝整个时间写入。"""
    issues = inspect_event_times(values)
    if issues:
        raise TimeValidationError(issues)


def precise_epoch(value: str | None) -> float | None:
    """SQLite 查询用的确定时刻（秒）；日期/旧问题值返回 None，不能猜成午夜。"""
    try:
        parsed = parse_time(value)  # type: ignore[arg-type]
        return parsed.timestamp() if isinstance(parsed, datetime) else None
    except TimeValueError:
        return None


def date_value(value: str | None) -> str | None:
    """仅返回有效 date；精确时间和待核对内容不参与日期分支。"""
    try:
        parsed = parse_time(value)  # type: ignore[arg-type]
        return parsed.isoformat() if type(parsed) is date else None
    except TimeValueError:
        return None


def time_sort_key(value: str | None, timezone: str) -> float | None:
    """时间排序键（秒）；date 放在展示日开始，仅用于排序，不改变事实精度。"""
    try:
        parsed = parse_time(value)  # type: ignore[arg-type]
        if isinstance(parsed, datetime):
            return parsed.timestamp()
        return datetime.combine(parsed, time.min, ZoneInfo(timezone)).timestamp()
    except (TimeValueError, OverflowError):
        return None

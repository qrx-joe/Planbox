"""ICS 导出：保留日期精度，任何待核对条目均需明确处理，不生成残缺文件。"""

from datetime import UTC, date, datetime, timedelta

from backend.app.core.temporal import MESSAGES, inspect_event_times, parse_time
from backend.app.schemas.event import EventRead


class CalendarTimeError(ValueError):
    """导出核对清单只包含实体 ID、字段与原因，不泄漏标题/来源原文。"""

    def __init__(self, issues: list[dict[str, str]]):
        self.issues = issues
        super().__init__("存在待核对时间，请修正后重新导出")


class CalendarService:
    def export_events(self, events: list[EventRead]) -> str:
        """返回 CRLF/UTF-8 的完整 ICS；有问题时抛 CalendarTimeError，无存储副作用。"""
        components: list[str] = []
        issues: list[dict[str, str]] = []
        for event in events:
            problems = inspect_event_times(event.model_dump())
            if problems:
                issues.extend(
                    {"event_id": event.id, "field": issue.field, "reason": MESSAGES[issue.code]}
                    for issue in problems
                )
                continue
            try:
                parts = self._event_to_components(event)
            except (ValueError, OverflowError):
                issues.append(
                    {
                        "event_id": event.id,
                        "field": "event_time",
                        "reason": "时间超出日历可表达范围",
                    }
                )
                continue
            if not parts:
                issues.append(
                    {
                        "event_id": event.id,
                        "field": "event_time",
                        "reason": "尚无可导出的确定日期或时刻",
                    }
                )
            for index, node in enumerate(event.schedule):
                if not node.get("start_at") and not node.get("end_at"):
                    issues.append(
                        {
                            "event_id": event.id,
                            "field": f"schedule.{index}",
                            "reason": "赛程节点尚无确定日期或时刻",
                        }
                    )
            components.extend(parts)
        if issues:
            raise CalendarTimeError(issues)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Planbox//AI Activity Opportunity Manager//CN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            *components,
            "END:VCALENDAR",
        ]
        return "\r\n".join(self._fold(line) for line in lines) + "\r\n"

    def _event_to_components(self, event: EventRead) -> list[str]:
        components: list[str] = []

        def add(uid: str, summary: str, start: str, end: str | None = None) -> None:
            components.extend(
                self._build_component(
                    uid, summary, start, end, self._description(event), event.location
                )
            )

        for field, label, suffix in (
            (event.registration_start_at, "报名开始", "registration-start"),
            (event.registration_deadline_at, "报名截止", "registration-deadline"),
        ):
            if field:
                add(f"{event.id}-{suffix}@planbox", f"{label}：{event.title}", field)
        if event.event_start_at:
            add(
                f"{event.id}-event-time@planbox",
                f"活动开始：{event.title}",
                event.event_start_at,
                event.event_end_at,
            )
        elif event.event_end_at:
            add(f"{event.id}-event-end@planbox", f"活动结束：{event.title}", event.event_end_at)
        for index, node in enumerate(event.schedule):
            start = node.get("start_at") or node.get("end_at")
            if start:
                label = node.get("name") or "赛程节点"
                if not node.get("start_at"):
                    label += "结束"
                add(
                    f"{event.id}-schedule-{index}@planbox",
                    f"{label}：{event.title}",
                    start,
                    node.get("end_at") if node.get("start_at") else None,
                )
        return components

    def _build_component(
        self,
        uid: str,
        summary: str,
        start_at: str,
        end_at: str | None,
        description: str,
        location: str | None,
    ) -> list[str]:
        start = parse_time(start_at)
        end = parse_time(end_at) if end_at else None
        time_lines: list[str]
        if isinstance(start, datetime):
            time_lines = [f"DTSTART:{self._format_utc(start)}"]
            if isinstance(end, datetime):
                time_lines.append(f"DTEND:{self._format_utc(end)}")
        else:
            time_lines = [f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}"]
            if isinstance(end, date):
                # 主办方区间含结束日；ICS DTEND 排他，需转换成下一日。
                time_lines.append(
                    f"DTEND;VALUE=DATE:{(end + timedelta(days=1)).strftime('%Y%m%d')}"
                )
        return [
            "BEGIN:VEVENT",
            f"UID:{self._escape(uid)}",
            f"DTSTAMP:{self._format_utc(datetime.now(UTC))}",
            *time_lines,
            f"SUMMARY:{self._escape(summary)}",
            f"DESCRIPTION:{self._escape(description)}",
            f"LOCATION:{self._escape(location or '')}",
            "END:VEVENT",
        ]

    def _description(self, event: EventRead) -> str:
        parts = [
            f"状态：{event.status}",
            f"类型：{event.event_type}" if event.event_type else None,
            f"报名链接：{event.registration_url}" if event.registration_url else None,
            f"缺失字段：{'、'.join(event.missing_fields)}" if event.missing_fields else None,
        ]
        return "\n".join(part for part in parts if part)

    def _format_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def _escape(self, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        return (
            normalized.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace(",", "\\,")
            .replace(";", "\\;")
        )

    def _fold(self, line: str) -> str:
        """每物理行最多 75 octets；续行空格计入长度，不截断 UTF-8 字符。"""
        parts: list[str] = []
        current = ""
        size = 0
        for character in line:
            width = len(character.encode("utf-8"))
            if size + width > 75:
                parts.append(current)
                current, size = " ", 1
            current += character
            size += width
        return "\r\n".join([*parts, current])

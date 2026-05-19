from datetime import UTC, datetime, timedelta

from backend.app.schemas.event import EventRead


class CalendarService:
    def export_events(self, events: list[EventRead]) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Planbox//AI Activity Opportunity Manager//CN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]
        for event in events:
            lines.extend(self._event_to_components(event))
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    def _event_to_components(self, event: EventRead) -> list[str]:
        components: list[str] = []
        if event.registration_deadline_at:
            components.extend(
                self._build_component(
                    uid=f"{event.id}-registration-deadline@planbox",
                    summary=f"报名截止：{event.title}",
                    start_at=event.registration_deadline_at,
                    end_at=None,
                    description=self._description(event),
                    location=event.location,
                )
            )
        if event.event_start_at:
            components.extend(
                self._build_component(
                    uid=f"{event.id}-event-time@planbox",
                    summary=f"活动开始：{event.title}",
                    start_at=event.event_start_at,
                    end_at=event.event_end_at,
                    description=self._description(event),
                    location=event.location,
                )
            )
        for index, node in enumerate(event.schedule):
            start_at = node.get("start_at")
            if not start_at:
                continue
            name = node.get("name") or "赛程节点"
            components.extend(
                self._build_component(
                    uid=f"{event.id}-schedule-{index}@planbox",
                    summary=f"{name}：{event.title}",
                    start_at=start_at,
                    end_at=node.get("end_at"),
                    description=self._description(event),
                    location=event.location,
                )
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
        start = self._parse_datetime(start_at)
        end = self._parse_datetime(end_at) if end_at else start + timedelta(minutes=30)
        return [
            "BEGIN:VEVENT",
            f"UID:{self._escape(uid)}",
            f"DTSTAMP:{self._format_utc(datetime.now(UTC))}",
            f"DTSTART:{self._format_utc(start)}",
            f"DTEND:{self._format_utc(end)}",
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

    def _parse_datetime(self, value: str | None) -> datetime:
        if not value:
            raise ValueError("Calendar datetime is required")
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def _format_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def _escape(self, value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace(",", "\\,")
            .replace(";", "\\;")
        )

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.schemas.event import EventRead
from backend.app.services.calendar_service import CalendarService, CalendarTimeError
from backend.app.services.event_service import EventService

router = APIRouter()
event_service = EventService()
calendar_service = CalendarService()


@router.get("/export.ics")
def export_actionable_calendar() -> Response:
    events = event_service.list_actionable()
    return _export(events, "planbox-actionable.ics")


@router.get("/events/{event_id}.ics")
def export_event_calendar(event_id: str) -> Response:
    event = event_service.get(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return _export([event], f"planbox-{event_id}.ics")


def _export(events: list[EventRead], filename: str) -> Response:
    try:
        content = calendar_service.export_events(events)
    except CalendarTimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CALENDAR_TIME_REVIEW_REQUIRED",
                "message": str(exc),
                "issues": exc.issues,
            },
        ) from exc
    return _calendar_response(content, filename)


def _calendar_response(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

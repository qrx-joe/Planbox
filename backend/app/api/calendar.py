from fastapi import APIRouter, HTTPException, Response, status

from backend.app.services.calendar_service import CalendarService
from backend.app.services.event_service import EventService

router = APIRouter()
event_service = EventService()
calendar_service = CalendarService()


@router.get("/export.ics")
def export_actionable_calendar() -> Response:
    events = event_service.list_actionable()
    return _calendar_response(calendar_service.export_events(events), "planbox-actionable.ics")


@router.get("/events/{event_id}.ics")
def export_event_calendar(event_id: str) -> Response:
    event = event_service.get(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return _calendar_response(calendar_service.export_events([event]), f"planbox-{event_id}.ics")


def _calendar_response(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

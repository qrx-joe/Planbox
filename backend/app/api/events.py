from fastapi import APIRouter

from backend.app.schemas.event import EventRead
from backend.app.services.event_service import EventService

router = APIRouter()
service = EventService()


@router.get("", response_model=list[EventRead])
def list_events() -> list[EventRead]:
    return service.list()

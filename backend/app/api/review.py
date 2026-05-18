from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.event import EventConfirm, EventRead
from backend.app.schemas.extraction import ReviewQueueItem
from backend.app.services.event_service import EventService
from backend.app.services.extraction_service import ExtractionService

router = APIRouter()
extraction_service = ExtractionService()
event_service = EventService(extraction_service=extraction_service)


@router.get("", response_model=list[ReviewQueueItem])
def list_review_queue() -> list[ReviewQueueItem]:
    return extraction_service.list_review_queue()


@router.post("/{extraction_result_id}/confirm", response_model=EventRead)
def confirm_extraction(extraction_result_id: str, payload: EventConfirm) -> EventRead:
    try:
        return event_service.confirm_extraction(extraction_result_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{extraction_result_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_extraction(extraction_result_id: str) -> None:
    if extraction_service.get(extraction_result_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction not found")
    extraction_service.reject(extraction_result_id)


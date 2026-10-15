from fastapi import APIRouter, HTTPException, status

from backend.app.repositories.extraction_repository import ExtractionRepository, loads_json
from backend.app.schemas.event import DuplicateCheckResponse, EventConfirm, EventRead
from backend.app.schemas.extraction import ReviewQueueItem
from backend.app.services.dedup_service import DedupService
from backend.app.services.event_service import EventService
from backend.app.services.extraction_service import ExtractionService

router = APIRouter()
extraction_service = ExtractionService()
extraction_repository = ExtractionRepository()
event_service = EventService()
dedup_service = DedupService()


@router.get("", response_model=list[ReviewQueueItem])
def list_review_queue() -> list[ReviewQueueItem]:
    return extraction_service.list_review_queue()


@router.post("/{extraction_result_id}/confirm", response_model=EventRead)
def confirm_extraction(extraction_result_id: str, payload: EventConfirm) -> EventRead:
    try:
        return event_service.confirm_extraction(extraction_result_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{extraction_result_id}/duplicates", response_model=DuplicateCheckResponse)
def check_draft_duplicates(extraction_result_id: str) -> DuplicateCheckResponse:
    """确认前预检：草稿是否与已有活动重复，让用户在入库前就决定是否合并。"""
    extraction = extraction_repository.get(extraction_result_id)
    if extraction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction not found")

    draft = loads_json(str(extraction["extracted_json"]), {})
    return dedup_service.check_draft(draft)


@router.post("/{extraction_result_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_extraction(extraction_result_id: str) -> None:
    if extraction_service.get(extraction_result_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction not found")
    extraction_service.reject(extraction_result_id)
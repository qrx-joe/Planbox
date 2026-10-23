from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from backend.app.repositories.extraction_repository import ExtractionRepository, loads_json
from backend.app.schemas.correction import CorrectionRetraction, DraftCorrectionList
from backend.app.schemas.event import DuplicateCheckResponse, EventConfirm, EventRead
from backend.app.schemas.extraction import DraftEdit, ExtractionResultRead, ReviewQueueItem
from backend.app.services.correction_service import CorrectionConflict
from backend.app.services.dedup_service import DedupService
from backend.app.services.draft_service import DraftService
from backend.app.services.event_service import EventService
from backend.app.services.extraction_service import ExtractionService

router = APIRouter()
draft_service = DraftService()
extraction_service = ExtractionService()
extraction_repository = ExtractionRepository()
event_service = EventService()
dedup_service = DedupService()


@router.get("", response_model=list[ReviewQueueItem])
def list_review_queue() -> list[ReviewQueueItem]:
    return extraction_service.list_review_queue()


def _review_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="草稿或更正记录不存在")
    if isinstance(exc, CorrectionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=400, detail="草稿字段格式不正确，请核对输入")
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/{extraction_result_id}", response_model=ExtractionResultRead)
def get_review(extraction_result_id: str) -> ExtractionResultRead:
    """重新读取保存的候选及版本；已确认/放弃的内容仍可只读回看。"""
    try:
        return draft_service.get(extraction_result_id)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc


@router.patch("/{extraction_result_id}", response_model=ExtractionResultRead)
def edit_review(extraction_result_id: str, payload: DraftEdit) -> ExtractionResultRead:
    """保存候选更正；未解决的时间明确保留为待核对，不绕过确认边界。"""
    try:
        return draft_service.update(extraction_result_id, payload)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc


@router.get("/{extraction_result_id}/corrections", response_model=DraftCorrectionList)
def list_draft_corrections(
    extraction_result_id: str,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),  # noqa: B008
) -> DraftCorrectionList:
    """分页返回入库前历史，不接受其他草稿的撤回身份。"""
    try:
        return draft_service.list(extraction_result_id, limit, offset)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc


@router.post("/{extraction_result_id}/corrections/{operation_id}/retract",
             response_model=ExtractionResultRead)
def retract_draft_correction(
    extraction_result_id: str, operation_id: str, payload: CorrectionRetraction,
) -> ExtractionResultRead:
    """只对待核对草稿整次恢复旧候选；后续更正覆盖时返回冲突。"""
    try:
        return draft_service.retract(extraction_result_id, operation_id, payload.reason)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc


@router.post("/{extraction_result_id}/confirm", response_model=EventRead)
def confirm_extraction(extraction_result_id: str, payload: EventConfirm) -> EventRead:
    try:
        return event_service.confirm_extraction(extraction_result_id, payload)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc


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
    try:
        draft_service.reject(extraction_result_id)
    except (KeyError, ValueError) as exc:
        raise _review_error(exc) from exc

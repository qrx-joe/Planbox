from fastapi import APIRouter

from backend.app.repositories.evidence_repository import EvidenceRepository
from backend.app.schemas.evidence import EvidenceListResponse, EvidenceRead
from backend.app.services.evidence_service import evidence_to_read

router = APIRouter()
repository = EvidenceRepository()


def _to_read(row: dict[str, object]) -> EvidenceRead:
    return evidence_to_read(row)


@router.get("", response_model=EvidenceListResponse)
def list_extraction_evidences(extraction_result_id: str) -> EvidenceListResponse:
    """草稿阶段的证据，用于待确认队列展示 AI 判断依据。"""
    items = repository.list_by_extraction(extraction_result_id)
    return EvidenceListResponse(
        items=[_to_read(row) for row in items], total=len(items)
    )


@router.get("/{event_id}", response_model=EvidenceListResponse)
def list_event_evidences(event_id: str) -> EvidenceListResponse:
    """某活动的全部字段级证据，用于活动详情展示溯源。"""
    items = repository.list_by_event(event_id)
    return EvidenceListResponse(items=[_to_read(row) for row in items], total=len(items))

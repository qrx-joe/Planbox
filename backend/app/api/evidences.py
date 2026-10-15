from fastapi import APIRouter

from backend.app.repositories.evidence_repository import EvidenceRepository
from backend.app.schemas.evidence import EvidenceListResponse, EvidenceRead

router = APIRouter()
repository = EvidenceRepository()


def _to_read(row: dict[str, object]) -> EvidenceRead:
    return EvidenceRead(
        id=str(row["id"]),
        event_id=row.get("event_id"),  # type: ignore[arg-type]
        extraction_result_id=row.get("extraction_result_id"),  # type: ignore[arg-type]
        source_item_id=str(row["source_item_id"]),
        field_name=str(row["field_name"]),
        field_value=row.get("field_value"),  # type: ignore[arg-type]
        evidence_text=row.get("evidence_text"),  # type: ignore[arg-type]
        confidence=row.get("confidence"),  # type: ignore[arg-type]
        source_title=row.get("source_title"),  # type: ignore[arg-type]
        source_url=row.get("source_url"),  # type: ignore[arg-type]
        created_at=str(row["created_at"]),
    )


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
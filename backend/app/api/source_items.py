from fastapi import APIRouter, HTTPException, UploadFile, status

from backend.app.schemas.extraction import ExtractionResultRead
from backend.app.schemas.source_item import (
    SourceItemCreate,
    SourceItemRead,
    SourceItemUploadResponse,
)
from backend.app.services.extraction_service import ExtractionService
from backend.app.services.source_service import SourceService

router = APIRouter()
service = SourceService()
extraction_service = ExtractionService()


@router.post("", response_model=SourceItemRead, status_code=status.HTTP_201_CREATED)
def create_source_item(payload: SourceItemCreate) -> SourceItemRead:
    return service.create(payload)


@router.get("", response_model=list[SourceItemRead])
def list_source_items() -> list[SourceItemRead]:
    return service.list()


@router.get("/{source_item_id}", response_model=SourceItemRead)
def get_source_item(source_item_id: str) -> SourceItemRead:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")
    return item


@router.post("/{source_item_id}/extract", response_model=ExtractionResultRead)
async def request_extraction(source_item_id: str) -> ExtractionResultRead:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")
    service.mark_extract_pending(source_item_id)
    return await extraction_service.extract(source_item_id)


@router.post("/{source_item_id}/assets", response_model=SourceItemUploadResponse)
async def upload_source_asset(source_item_id: str, file: UploadFile) -> SourceItemUploadResponse:
    item = service.get(source_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source item not found")

    asset = await service.save_asset(source_item_id, file)
    return asset

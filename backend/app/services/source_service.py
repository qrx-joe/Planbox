from pathlib import Path

from fastapi import UploadFile

from backend.app.core.config import settings
from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.source_item_repository import SourceItemRepository
from backend.app.schemas.source_item import (
    SourceItemCreate,
    SourceItemRead,
    SourceItemUploadResponse,
)


class SourceService:
    def __init__(self, repository: SourceItemRepository | None = None) -> None:
        self.repository = repository or SourceItemRepository()

    def create(self, payload: SourceItemCreate) -> SourceItemRead:
        timestamp = now_iso()
        row = self.repository.create(
            {
                "id": new_id("src"),
                "source_type": payload.source_type,
                "source_url": str(payload.source_url) if payload.source_url else None,
                "raw_text": payload.raw_text,
                "title": payload.title,
                "published_at": payload.published_at,
                "captured_at": timestamp,
                "parse_status": "unparsed",
                "parse_error": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return SourceItemRead.model_validate(row)

    def list(self) -> list[SourceItemRead]:
        return [SourceItemRead.model_validate(row) for row in self.repository.list()]

    def get(self, source_item_id: str) -> SourceItemRead | None:
        row = self.repository.get(source_item_id)
        return SourceItemRead.model_validate(row) if row else None

    def mark_extract_pending(self, source_item_id: str) -> None:
        self.repository.update_parse_status(source_item_id, "pending", now_iso())

    async def save_asset(self, source_item_id: str, file: UploadFile) -> SourceItemUploadResponse:
        timestamp = now_iso()
        asset_id = new_id("asset")
        suffix = Path(file.filename or "").suffix
        target_dir = settings.upload_dir / source_item_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{asset_id}{suffix}"

        contents = await file.read()
        target_path.write_bytes(contents)

        row = self.repository.create_asset(
            {
                "id": asset_id,
                "source_item_id": source_item_id,
                "asset_type": "image" if (file.content_type or "").startswith("image/") else "file",
                "file_path": str(target_path),
                "original_filename": file.filename,
                "mime_type": file.content_type,
                "created_at": timestamp,
            }
        )
        return SourceItemUploadResponse.model_validate(row)

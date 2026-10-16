from __future__ import annotations

import json
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
        return self.create_with_meta(
            source_type=payload.source_type,
            source_url=str(payload.source_url) if payload.source_url else None,
            raw_text=payload.raw_text,
            title=payload.title,
            published_at=payload.published_at,
            ingest_state="ready",
            source_meta=None,
        )

    def create_with_meta(
        self,
        *,
        source_type: str,
        source_url: str | None,
        raw_text: str | None,
        title: str | None,
        published_at: str | None,
        ingest_state: str,
        source_meta: dict[str, object] | None,
    ) -> SourceItemRead:
        """带来源元信息的创建。分享入口用它记录 scene 等原始参数。"""
        timestamp = now_iso()
        row = self.repository.create(
            {
                "id": new_id("src"),
                "source_type": source_type,
                "source_url": source_url,
                "raw_text": raw_text,
                "title": title,
                "published_at": published_at,
                "captured_at": timestamp,
                "parse_status": "unparsed",
                "parse_error": None,
                "ingest_state": ingest_state,
                "source_meta": (
                    json.dumps(source_meta, ensure_ascii=False) if source_meta else None
                ),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return self._to_read(row)

    def update_content(
        self,
        *,
        source_item_id: str,
        raw_text: str,
        title: str | None,
        published_at: str | None,
    ) -> SourceItemRead:
        """用户粘贴正文后的补全。会清掉旧的 parse_error 并重置为待解析。"""
        timestamp = now_iso()
        row = self.repository.update_content(
            source_item_id=source_item_id,
            raw_text=raw_text,
            title=title,
            published_at=published_at,
            ingest_state="ready",
            parse_status="unparsed",
            updated_at=timestamp,
        )
        return self._to_read(row)

    def list(self) -> list[SourceItemRead]:
        return [self._to_read(row) for row in self.repository.list()]

    def get(self, source_item_id: str) -> SourceItemRead | None:
        row = self.repository.get(source_item_id)
        return self._to_read(row) if row else None

    def _to_read(self, row: dict[str, object]) -> SourceItemRead:
        """DB 行 → schema。source_meta 是 JSON 字符串，需要单独解析。"""
        return SourceItemRead(
            id=str(row["id"]),
            source_type=str(row["source_type"]),  # type: ignore[arg-type]
            source_url=row.get("source_url"),  # type: ignore[arg-type]
            raw_text=row.get("raw_text"),  # type: ignore[arg-type]
            title=row.get("title"),  # type: ignore[arg-type]
            published_at=row.get("published_at"),  # type: ignore[arg-type]
            captured_at=str(row["captured_at"]),
            parse_status=str(row["parse_status"]),  # type: ignore[arg-type]
            parse_error=row.get("parse_error"),  # type: ignore[arg-type]
            ingest_state=row.get("ingest_state"),  # type: ignore[arg-type]
            source_meta=_loads_meta(row.get("source_meta")),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def mark_extract_pending(self, source_item_id: str) -> None:
        self.repository.update_parse_status(source_item_id, "pending", now_iso())

    async def save_asset(
        self, source_item_id: str, file: UploadFile
    ) -> SourceItemUploadResponse:
        # 读之前先看声明大小，能提前拒绝就不用占用内存
        declared = file.size or 0
        if declared > settings.max_upload_bytes:
            raise ValueError(
                f"文件超过上限 {settings.max_upload_bytes // (1024 * 1024)} MB"
            )

        contents = await file.read()
        # chunked 传输时 Content-Length 不可信，实际字节数才是准的
        if len(contents) > settings.max_upload_bytes:
            raise ValueError(
                f"文件超过上限 {settings.max_upload_bytes // (1024 * 1024)} MB"
            )

        timestamp = now_iso()
        asset_id = new_id("asset")
        suffix = Path(file.filename or "").suffix
        target_dir = settings.upload_dir / source_item_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{asset_id}{suffix}"

        target_path.write_bytes(contents)

        row = self.repository.create_asset(
            {
                "id": asset_id,
                "source_item_id": source_item_id,
                "asset_type": (
                    "image" if (file.content_type or "").startswith("image/") else "file"
                ),
                "file_path": str(target_path),
                "original_filename": file.filename,
                "mime_type": file.content_type,
                "created_at": timestamp,
            }
        )
        return SourceItemUploadResponse.model_validate(row)


def _loads_meta(raw: object) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None

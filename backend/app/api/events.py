
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from backend.app.core.temporal import TimeValidationError
from backend.app.repositories.event_repository import EventQuery
from backend.app.schemas.correction import CorrectionList, CorrectionRetraction
from backend.app.schemas.event import (
    DuplicateCheckResponse,
    EventListResponse,
    EventMergeRequest,
    EventMergeResponse,
    EventRead,
    EventStatusUpdate,
    EventUpdate,
)
from backend.app.schemas.opportunity_score import OpportunityScoreRead
from backend.app.services.correction_service import CorrectionConflict, CorrectionService
from backend.app.services.dedup_service import DedupService
from backend.app.services.event_service import EventService, EventStatusError, event_row_to_read
from backend.app.services.scoring_service import ScoringService

router = APIRouter()
service = EventService()
scoring_service = ScoringService()
dedup_service = DedupService()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")


# ruff: noqa: B008  —— FastAPI 依赖注入的标准写法，Query 必须在默认值位置调用
@router.get("", response_model=EventListResponse | list[EventRead])
def list_events(
    status_filter: list[str] | None = Query(None, alias="status"),
    keyword: str | None = None,
    event_type: str | None = None,
    is_online: bool | None = None,
    location: str | None = None,
    deadline_before: str | None = None,
    deadline_after: str | None = None,
    event_start_after: str | None = None,
    event_start_before: str | None = None,
    only_actionable: bool = False,
    has_missing_fields: bool | None = None,
    sort: str = "deadline",
    order: str = "asc",
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    paginated: bool = True,
) -> EventListResponse | list[EventRead]:
    """活动列表。支持按状态、关键词、类型、线上线下、地点、时间区间筛选与排序。

    默认返回分页结构 {items, total}；传 paginated=false 时返回纯数组，兼容旧客户端。
    """
    try:
        query = EventQuery(
            status=status_filter,
            keyword=keyword,
            event_type=event_type,
            is_online=is_online,
            location=location,
            deadline_before=deadline_before,
            deadline_after=deadline_after,
            event_start_after=event_start_after,
            event_start_before=event_start_before,
            only_actionable=only_actionable,
            has_missing_fields=has_missing_fields,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    except (TimeValidationError, OverflowError) as exc:
        detail = str(exc) if isinstance(exc, TimeValidationError) else "日期超出可查询范围"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    if paginated:
        return service.list_paginated(query)
    return service.list(query)


@router.get("/duplicates/{event_id}", response_model=DuplicateCheckResponse)
def check_duplicates(event_id: str) -> DuplicateCheckResponse:
    """检查某活动是否存在重复项。不存在时返回 candidates 为空。"""
    if service.get(event_id) is None:
        raise _not_found()
    return dedup_service.find_duplicates(event_id)


@router.post("/merge", response_model=EventMergeResponse)
def merge_events(payload: EventMergeRequest) -> EventMergeResponse:
    """把重复活动合并进保留活动：用source 补全 target 的空字段，保留多来源证据。"""
    try:
        return dedup_service.merge(payload.target_id, payload.source_id)
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{event_id}", response_model=EventRead)
def get_event(event_id: str) -> EventRead:
    event = service.get(event_id)
    if event is None:
        raise _not_found()
    return event


@router.patch("/{event_id}", response_model=EventRead)
def update_event(event_id: str, payload: EventUpdate) -> EventRead:
    """修正活动字段。只传需要修改的字段，未传的字段保持不变。"""
    try:
        return service.update(event_id, payload)
    except KeyError as exc:
        raise _not_found() from exc
    except CorrectionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EventStatusError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TimeValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{event_id}/corrections", response_model=CorrectionList)
def list_corrections(
    event_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
) -> CorrectionList:
    """分页读取此活动的人工更正；来源定位不等于人工确认，主体未认证。"""
    try:
        return CorrectionService().list(event_id, limit, offset)
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/{event_id}/corrections/{operation_id}/retract", response_model=EventRead)
def retract_correction(
    event_id: str, operation_id: str, payload: CorrectionRetraction,
) -> EventRead:
    """整次撤回并保留反向历史；后续修改覆盖时返回 409，重复成功请求不再写入。"""
    try:
        restored = CorrectionService().retract(event_id, operation_id, payload.reason)
        return event_row_to_read(restored)
    except KeyError as exc:
        raise _not_found() from exc
    except CorrectionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="旧值不满足当前字段契约，无法直接恢复") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/{event_id}/status", response_model=EventRead)
def update_event_status(event_id: str, payload: EventStatusUpdate) -> EventRead:
    """流转活动状态。会校验迁移路径，非法流转返回 400。"""
    try:
        return service.update_status(event_id, payload)
    except KeyError as exc:
        raise _not_found() from exc
    except EventStatusError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: str) -> None:
    try:
        service.delete(event_id)
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/{event_id}/score", response_model=OpportunityScoreRead)
def score_event(event_id: str) -> OpportunityScoreRead:
    event = service.get(event_id)
    if event is None:
        raise _not_found()
    return scoring_service.score_event(event.model_dump())


@router.get("/{event_id}/score", response_model=OpportunityScoreRead)
def get_event_score(event_id: str) -> OpportunityScoreRead:
    score = scoring_service.get_score(event_id)
    if score is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Score not found")
    return score

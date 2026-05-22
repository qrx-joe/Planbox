from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.event import EventRead
from backend.app.schemas.opportunity_score import OpportunityScoreRead
from backend.app.services.event_service import EventService
from backend.app.services.scoring_service import ScoringService

router = APIRouter()
service = EventService()
scoring_service = ScoringService()


@router.get("", response_model=list[EventRead])
def list_events() -> list[EventRead]:
    return service.list()


@router.post("/{event_id}/score", response_model=OpportunityScoreRead)
def score_event(event_id: str) -> OpportunityScoreRead:
    event = service.get(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return scoring_service.score_event(event.model_dump())


@router.get("/{event_id}/score", response_model=OpportunityScoreRead)
def get_event_score(event_id: str) -> OpportunityScoreRead:
    score = scoring_service.get_score(event_id)
    if score is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Score not found")
    return score

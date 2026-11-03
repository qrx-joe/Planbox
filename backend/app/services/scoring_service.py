from __future__ import annotations

from backend.app.core.ids import new_id
from backend.app.core.time import now_iso
from backend.app.repositories.opportunity_score_repository import OpportunityScoreRepository
from backend.app.schemas.opportunity_score import OpportunityScoreRead


class ScoringService:
    def __init__(self, repository: OpportunityScoreRepository | None = None) -> None:
        self.repository = repository or OpportunityScoreRepository()

    def score_event(self, event: dict[str, object]) -> OpportunityScoreRead:
        event_id = str(event["id"])
        existing = self.repository.get_by_event(event_id)
        if existing is not None:
            return self._to_read(existing)

        # Rule-based scoring (first version)
        scores = self._calculate_scores(event)

        timestamp = now_iso()
        row = self.repository.create(
            {
                "id": new_id("scr"),
                "event_id": event_id,
                "fit_score": scores["fit_score"],
                "urgency_score": scores["urgency_score"],
                "portfolio_value": scores["portfolio_value"],
                "networking_value": scores["networking_value"],
                "learning_value": scores["learning_value"],
                "time_cost": scores["time_cost"],
                "recommendation": scores["recommendation"],
                "reason_to_join": scores["reason_to_join"],
                "reason_to_skip": scores["reason_to_skip"],
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return self._to_read(row)

    def _calculate_scores(self, event: dict[str, object]) -> dict[str, object]:
        # Simple rule-based scoring
        fit_score = 50
        urgency_score = 50
        portfolio_value = 50
        networking_value = 50
        learning_value = 50

        # Urgency based on registration deadline
        if event.get("registration_deadline_at"):
            urgency_score = 70

        # Portfolio value for hackathons and competitions
        event_type = str(event.get("event_type") or "").lower()
        if event_type in ("hackathon", "competition"):
            portfolio_value = 80
            learning_value = 75
        elif event_type in ("bootcamp", "workshop"):
            learning_value = 85
            portfolio_value = 60
        elif event_type == "conference":
            networking_value = 80
            learning_value = 70

        # Has prize = higher portfolio value
        if event.get("prize"):
            portfolio_value = max(portfolio_value, 75)

        # Team required = more networking
        if event.get("team_required"):
            networking_value = max(networking_value, 70)

        # Calculate overall fit
        fit_score = int((portfolio_value + networking_value + learning_value) / 3)

        # Recommendation
        if fit_score >= 70 and urgency_score >= 60:
            recommendation = "推荐报名"
            reason_to_join = "高匹配度且时间紧迫，建议尽快报名"
            reason_to_skip = None
        elif fit_score >= 60:
            recommendation = "值得关注"
            reason_to_join = "与目标有一定匹配，可根据时间安排决定"
            reason_to_skip = "如果时间冲突或优先级不高，可考虑放弃"
        else:
            recommendation = "观望或放弃"
            reason_to_join = None
            reason_to_skip = "匹配度一般，除非有特定需求否则不建议投入时间"

        return {
            "fit_score": fit_score,
            "urgency_score": urgency_score,
            "portfolio_value": portfolio_value,
            "networking_value": networking_value,
            "learning_value": learning_value,
            "time_cost": "中等" if event_type in ("hackathon", "competition") else "低",
            "recommendation": recommendation,
            "reason_to_join": reason_to_join,
            "reason_to_skip": reason_to_skip,
        }

    def get_score(self, event_id: str) -> OpportunityScoreRead | None:
        row = self.repository.get_by_event(event_id)
        return self._to_read(row) if row else None

    def _to_read(self, row: dict[str, object]) -> OpportunityScoreRead:
        return OpportunityScoreRead(
            id=str(row["id"]),
            event_id=str(row["event_id"]),
            fit_score=row.get("fit_score"),
            urgency_score=row.get("urgency_score"),
            portfolio_value=row.get("portfolio_value"),
            networking_value=row.get("networking_value"),
            learning_value=row.get("learning_value"),
            time_cost=row.get("time_cost"),
            recommendation=row.get("recommendation"),
            reason_to_join=row.get("reason_to_join"),
            reason_to_skip=row.get("reason_to_skip"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

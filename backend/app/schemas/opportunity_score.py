from pydantic import BaseModel, Field


class OpportunityScoreRead(BaseModel):
    id: str
    event_id: str
    fit_score: int | None = Field(default=None, ge=0, le=100)
    urgency_score: int | None = Field(default=None, ge=0, le=100)
    portfolio_value: int | None = Field(default=None, ge=0, le=100)
    networking_value: int | None = Field(default=None, ge=0, le=100)
    learning_value: int | None = Field(default=None, ge=0, le=100)
    time_cost: str | None = None
    recommendation: str | None = None
    reason_to_join: str | None = None
    reason_to_skip: str | None = None
    created_at: str
    updated_at: str


class OpportunityScoreCreate(BaseModel):
    event_id: str
    fit_score: int | None = Field(default=None, ge=0, le=100)
    urgency_score: int | None = Field(default=None, ge=0, le=100)
    portfolio_value: int | None = Field(default=None, ge=0, le=100)
    networking_value: int | None = Field(default=None, ge=0, le=100)
    learning_value: int | None = Field(default=None, ge=0, le=100)
    time_cost: str | None = None
    recommendation: str | None = None
    reason_to_join: str | None = None
    reason_to_skip: str | None = None

from pydantic import BaseModel, Field


class UserPreferenceCreate(BaseModel):
    goals: str | None = Field(default=None, max_length=1000)
    interested_topics: str | None = Field(default=None, max_length=500)
    blocked_topics: str | None = Field(default=None, max_length=500)
    preferred_locations: str | None = Field(default=None, max_length=500)
    online_only: bool | None = None
    available_time: str | None = Field(default=None, max_length=500)


class UserPreferenceRead(BaseModel):
    id: str
    goals: str | None = None
    interested_topics: str | None = None
    blocked_topics: str | None = None
    preferred_locations: str | None = None
    online_only: bool | None = None
    available_time: str | None = None
    created_at: str
    updated_at: str

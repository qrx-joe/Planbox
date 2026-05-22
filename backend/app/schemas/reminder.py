from typing import Literal

from pydantic import BaseModel

ReminderType = Literal["registration_deadline", "event_start", "custom"]
ReminderStatus = Literal["pending", "sent", "dismissed"]
ReminderChannel = Literal["ics", "email", "push"]


class ReminderCreate(BaseModel):
    event_id: str
    reminder_type: ReminderType
    trigger_at: str
    channel: ReminderChannel = "ics"


class ReminderRead(BaseModel):
    id: str
    event_id: str
    event_title: str | None = None
    reminder_type: ReminderType
    trigger_at: str
    channel: ReminderChannel
    status: ReminderStatus
    created_at: str
    updated_at: str


class ReminderUpdate(BaseModel):
    status: ReminderStatus | None = None
    trigger_at: str | None = None

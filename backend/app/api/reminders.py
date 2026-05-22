from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.reminder import ReminderCreate, ReminderRead, ReminderUpdate
from backend.app.services.reminder_service import ReminderService

router = APIRouter()
service = ReminderService()


@router.get("", response_model=list[ReminderRead])
def list_reminders() -> list[ReminderRead]:
    return service.list()


@router.post("", response_model=ReminderRead, status_code=status.HTTP_201_CREATED)
def create_reminder(payload: ReminderCreate) -> ReminderRead:
    return service.create(payload)


@router.patch("/{reminder_id}", response_model=ReminderRead)
def update_reminder(reminder_id: str, payload: ReminderUpdate) -> ReminderRead:
    result = service.update(reminder_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found")
    return result


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(reminder_id: str) -> None:
    if not service.delete(reminder_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found")

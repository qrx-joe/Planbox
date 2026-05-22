from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.user_preference import UserPreferenceCreate, UserPreferenceRead
from backend.app.services.user_preference_service import UserPreferenceService

router = APIRouter()
service = UserPreferenceService()


@router.get("/latest", response_model=UserPreferenceRead)
def get_latest_preferences() -> UserPreferenceRead:
    result = service.get_latest()
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preferences found")
    return result


@router.post("", response_model=UserPreferenceRead, status_code=status.HTTP_201_CREATED)
def create_preferences(payload: UserPreferenceCreate) -> UserPreferenceRead:
    return service.create(payload)


@router.patch("/{preference_id}", response_model=UserPreferenceRead)
def update_preferences(preference_id: str, payload: UserPreferenceCreate) -> UserPreferenceRead:
    result = service.update(preference_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preferences not found")
    return result

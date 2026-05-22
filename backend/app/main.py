from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.calendar import router as calendar_router
from backend.app.api.events import router as events_router
from backend.app.api.reminders import router as reminders_router
from backend.app.api.review import router as review_router
from backend.app.api.source_items import router as source_items_router
from backend.app.api.user_preferences import router as user_preferences_router
from backend.app.core.config import settings
from backend.app.db.database import initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Planbox API",
    version="0.1.0",
    description="AI activity opportunity manager API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(source_items_router, prefix="/api/source-items", tags=["source-items"])
app.include_router(review_router, prefix="/api/review-queue", tags=["review-queue"])
app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(reminders_router, prefix="/api/reminders", tags=["reminders"])
app.include_router(user_preferences_router, prefix="/api/user-preferences", tags=["user-preferences"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

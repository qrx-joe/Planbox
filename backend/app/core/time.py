from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.core.config import settings


def now_iso() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")


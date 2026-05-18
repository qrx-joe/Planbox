from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.core.config import settings


def now_shanghai() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def now_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")

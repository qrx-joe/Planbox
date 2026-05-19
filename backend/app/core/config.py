from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Planbox"
    timezone: str = "Asia/Shanghai"
    data_dir: Path = Field(default=Path("data"))
    database_path: Path = Field(default=Path("data/planbox.db"))
    upload_dir: Path = Field(default=Path("data/uploads"))
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    llm_provider: Literal["openai", "fake"] = "fake"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


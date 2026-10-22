import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 项目根 = backend/app/core/config.py 上溯三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Planbox"
    timezone: str = "Asia/Shanghai"

    # 数据目录。相对路径必须锚定到项目根，而不是进程 CWD——
    # 生产环境 CWD 随启动方式变化，用相对路径会写到意料之外的目录。
    project_root: Path = Field(default=PROJECT_ROOT)
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    database_path: Path = Field(default=PROJECT_ROOT / "data" / "planbox.db")
    upload_dir: Path = Field(default=PROJECT_ROOT / "data" / "uploads")

    # CORS 白名单。环境变量支持逗号分隔，并兼容已有 JSON 数组。
    # 通配符仅用于本地验证公网连通性，生产必须显式列出域名。
    # 由 validator 统一解析，避免 settings 在 validator 前强制 JSON 解码。
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    llm_provider: Literal["openai", "fake"] = "fake"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # 单次上传上限。容器文件系统通常只有几百 MB，不限迟早爆盘。
    max_upload_bytes: int = 10 * 1024 * 1024

    # 微信抓取超时。云端 IP 常被风控，快速失败并降级比长时间挂起更好。
    wechat_fetch_timeout: int = 10

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        """解析逗号字符串或 JSON 数组；保留 Python list、空数组及空字符串旧行为。"""
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
            items = [item.strip() for item in value.split(",") if item.strip()]
            return items or ["http://localhost:5173"]
        return value

    @field_validator("data_dir", "database_path", "upload_dir", mode="after")
    @classmethod
    def anchor_to_root(cls, value: Path) -> Path:
        """相对路径锚定到项目根，避免因 CWD 不同写错位置。"""
        return value if value.is_absolute() else PROJECT_ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

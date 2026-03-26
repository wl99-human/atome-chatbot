from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Atome Support Bot"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{(BASE_DIR / 'atome_chatbot.db').as_posix()}"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash-lite"
    default_kb_url: str = (
        "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card"
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    auto_sync_default_agent: bool = True
    max_sync_articles: int = 80
    request_timeout_seconds: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()

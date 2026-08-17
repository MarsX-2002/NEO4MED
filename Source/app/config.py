"""Конфигурация из .env с проверкой на старте.

Принцип: если чего-то не хватает, процесс падает сразу с понятным сообщением,
а не через десять минут посреди диалога с медсестрой.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SOURCE_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── База ──────────────────────────────────────────────────────────────────
    # Приложение обязано ходить под урезанной ролью ishmed_app, а не под
    # владельцем ezgumed: иначе гарантии приватности контактов ничего не стоят.
    app_database_url: SecretStr = Field(alias="APP_DATABASE_URL")
    db_pool_min: int = 1
    db_pool_max: int = 8

    # Подключение владельца. Нужно только тестам и служебным скриптам:
    # подготовить или убрать данные. Прикладной код им пользоваться не должен —
    # иначе смысл разделения ролей пропадает.
    admin_database_url: SecretStr | None = Field(None, alias="DATABASE_URL")

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_endpoint: str = Field(alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: SecretStr = Field(alias="AZURE_OPENAI_API_KEY")
    azure_openai_api_version: str = Field(alias="AZURE_OPENAI_API_VERSION")

    # Замерено на живых вызовах: gpt-5 с рассуждением по умолчанию отвечает
    # ~10 c, что для чата неприемлемо. Диалог ведёт mini с minimal-рассуждением
    # (~1.7 c), извлечение полей — старшая модель (~2.3 c при minimal).
    dialog_deployment: str = Field("gpt-5-mini", alias="AZURE_OPENAI_DIALOG_DEPLOYMENT")
    extract_deployment: str = Field("gpt-5", alias="AZURE_OPENAI_CHAT_DEPLOYMENT")
    stt_deployment: str = Field("gpt-4o-transcribe", alias="AZURE_OPENAI_STT_DEPLOYMENT")
    embedding_deployment: str = Field(
        "text-embedding-3-small", alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
    )
    # Озвучка вопросов интервью. Берём обычный tts, а не аудио-модель: текст
    # вопроса уже написан и одобрен менеджером, модели остаётся только его
    # прочитать. Замерено 2.0 c на вопрос, отдаёт ogg/opus — ровно то, что
    # Telegram принимает как голосовое сообщение без перекодирования.
    tts_deployment: str = Field("tts", alias="AZURE_OPENAI_TTS_DEPLOYMENT")
    tts_voice: str = Field("alloy", alias="AZURE_OPENAI_TTS_VOICE")
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "minimal"

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field("ishmedbot", alias="TELEGRAM_BOT_USERNAME")

    # Второй бот — приём отзывов от пациентов по QR. Отдельный от бота медиков
    # намеренно: разные аудитории и разные разговоры.
    review_bot_token: SecretStr | None = Field(None, alias="REVIEW_BOT_TOKEN")
    review_bot_username: str = Field("ishmedsifatbot", alias="REVIEW_BOT_USERNAME")

    # ── Веб-платформа клиник ──────────────────────────────────────────────────
    web_host: str = Field("127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(8000, alias="WEB_PORT")
    web_secret_key: SecretStr = Field(alias="WEB_SECRET_KEY")
    web_base_url: str = Field("https://ishmed.ezgupro.uz", alias="WEB_BASE_URL")
    session_ttl_hours: int = 12

    # ── Демо ──────────────────────────────────────────────────────────────────
    demo_mode: bool = Field(False, alias="DEMO_MODE")
    consent_version: str = Field("2026-08-16", alias="CONSENT_VERSION")

    @field_validator("azure_openai_endpoint", "web_base_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def dsn(self) -> str:
        return self.app_database_url.get_secret_value()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

"""Configuration, read once from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _path(value: str, default: Path) -> Path:
    resolved = Path(value) if value else default
    return resolved if resolved.is_absolute() else (PROJECT_ROOT / resolved).resolve()


class Settings:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Extraction is high volume and mechanical; scoring is judgement on a handful.
    EXTRACT_MODEL: str = os.getenv("EXTRACT_MODEL", "claude-sonnet-5")
    SCORE_MODEL: str = os.getenv("SCORE_MODEL", "claude-opus-5")
    LLM_EFFORT: str = os.getenv("LLM_EFFORT", "medium").strip().lower()

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    COMPANIES_HOUSE_KEY: str = os.getenv("COMPANIES_HOUSE_KEY", "")
    SITE_PASSPHRASE: str = os.getenv("SITE_PASSPHRASE", "")

    CONFIG_PATH: Path = PROJECT_ROOT / "config" / "sources.yaml"
    DATABASE_PATH: Path = _path(os.getenv("DATABASE_PATH", ""), PROJECT_ROOT / "data" / "funding.db")
    SITE_DIR: Path = _path(os.getenv("SITE_DIR", ""), PROJECT_ROOT / "site")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    MAX_EXTRACTIONS_PER_RUN: int = int(os.getenv("MAX_EXTRACTIONS_PER_RUN", "250"))
    HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "30"))
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    )

    def require_llm(self) -> None:
        if not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required. Copy .env.example to .env and set it.")


settings = Settings()

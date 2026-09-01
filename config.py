"""
Production-grade configuration with support for ALL AI providers.
Pydantic Settings — full validation, auto-creates required dirs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google Gemini ──────────────────────────────────────────────────────────
    gemini_api_key: Optional[str] = Field(default=None)
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model name",
    )
    gemini_model_pro: str   = Field(default="gemini-2.5-pro")
    gemini_model_flash: str = Field(default="gemini-2.5-flash")
    gemini_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    gemini_max_output_tokens: int = Field(default=8192)

    # ── OpenAI GPT ────────────────────────────────────────────────────────────
    openai_api_key: Optional[str] = Field(default=None)
    openai_model: str      = Field(default="gpt-4o")
    openai_model_fast: str = Field(default="gpt-4o-mini")
    openai_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    openai_max_tokens: int    = Field(default=4096)

    # ── Anthropic Claude ──────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = Field(default=None)
    anthropic_model: str      = Field(default="claude-sonnet-4-5")
    anthropic_model_fast: str = Field(default="claude-haiku-4-5")
    anthropic_max_tokens: int = Field(default=4096)

    # ── Ollama (Local, fully offline) ─────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str    = Field(default="llama3.2")
    ollama_timeout: int  = Field(default=120)

    # ── OpenRouter (200+ models, many FREE) ───────────────────────────────────
    # Free API key: https://openrouter.ai/
    openrouter_api_key: Optional[str] = Field(default=None)
    # Primary model (leave blank to always use free models)
    openrouter_model: str = Field(default="meta-llama/llama-3.1-8b-instruct:free")
    # Comma-separated list of free models to cycle through
    openrouter_free_models: str = Field(
        default=(
            "nvidia/nemotron-3-ultra-550b-a55b:free,"
            "google/gemma-4-31b-it:free,"
            "nvidia/nemotron-3-super-120b-a12b:free,"
            "google/gemma-4-26b-a4b-it:free,"
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
        )
    )
    openrouter_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    openrouter_max_tokens: int    = Field(default=4096)

    # ── Groq (FREE ultra-fast inference) ──────────────────────────────────────
    # Free API key: https://console.groq.com/
    groq_api_key: Optional[str] = Field(default=None)
    # Leave blank to auto-select best model per task
    groq_model: str = Field(default="")
    groq_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    groq_max_tokens: int    = Field(default=4096)

    # ── Platform Tokens ───────────────────────────────────────────────────────
    discord_token:  Optional[str] = Field(default=None)
    telegram_token: Optional[str] = Field(default=None)
    
    # ── Slack ──────────────────────────────────────────────────────────────────────
    slack_bot_token: Optional[str] = Field(default=None)   # xoxb-...
    slack_app_token: Optional[str] = Field(default=None)   # xapp-... (for Socket Mode)
    slack_signing_secret: Optional[str] = Field(default=None)

    # ── Database ──────────────────────────────────────────────────────────────
    # ── Database (PostgreSQL — primary for production) ──────────────────────────
    database_url: Optional[str] = Field(
        default=None,
        description="PostgreSQL connection URL. If set, overrides SQLite for UnifiedMemory and LangGraph checkpoints."
    )
    # SQLite is kept as a local fallback when DATABASE_URL is not set
    db_path: str = Field(default="data/memory.sqlite")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_requests_per_minute: int = Field(default=20)
    rate_limit_tokens_per_day: int      = Field(default=200_000)

    # ── Bot Behaviour ─────────────────────────────────────────────────────────
    bot_name:             str = Field(default="OmniAgent")
    bot_prefix:           str = Field(default="!")
    max_history_messages: int = Field(default=50)

    # ── Multi-Agent Routing ───────────────────────────────────────────────────
    default_provider:  str = Field(default="auto")
    coding_provider:   str = Field(default="auto")
    creative_provider: str = Field(default="auto")
    math_provider:     str = Field(default="auto")
    quick_provider:    str = Field(default="auto")

    # Ordered fallback chain. Free providers (groq, openrouter) come after
    # premium ones; ollama is the final local fallback.
    fallback_order: str = Field(
        default="gemini,openai,anthropic,groq,openrouter,ollama"
    )

    # Health monitor
    health_check_interval_seconds: int = Field(default=300)
    model_failure_threshold:       int = Field(default=3)
    model_recovery_seconds:        int = Field(default=300)

    # ── Optional Features ─────────────────────────────────────────────────────
    openweathermap_api_key: Optional[str] = Field(default=None)

    # ── Logging ───────────────────────────────────────────────────────────────
    admin_api_secret: Optional[str] = Field(default=None)
    # ── Logging ───────────────────────────────────────────────────────────────
    log_level:       str = Field(default="INFO")
    log_file:        str = Field(default="logs/omniagent.log")
    log_max_bytes:   int = Field(default=10_485_760)
    log_backup_count: int = Field(default=5)

    # ── Computed Properties ───────────────────────────────────────────────────

    @property
    def fallback_order_list(self) -> list[str]:
        return [p.strip().lower() for p in self.fallback_order.split(",") if p.strip()]

    @property
    def openrouter_free_models_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_free_models.split(",") if m.strip()]

    @property
    def use_postgres(self) -> bool:
        """True when a PostgreSQL DATABASE_URL has been configured."""
        return bool(self.database_url)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return v.upper()

    @field_validator("db_path")
    @classmethod
    def ensure_db_dir(cls, v: str) -> str:
        Path(v).parent.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("log_file")
    @classmethod
    def ensure_log_dir(cls, v: str) -> str:
        Path(v).parent.mkdir(parents=True, exist_ok=True)
        return v


# Singleton
settings = Settings()

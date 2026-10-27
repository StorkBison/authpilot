from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./var/authpilot.db"
    fhir_data_dir: Path = ROOT / "data" / "fhir"
    fhir_base_url: str | None = None

    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"
    agent_max_steps: int = Field(default=30, ge=5, le=100)

    payer_decision_after_checks: int = Field(default=1, ge=0, le=10)
    coordinator_name: str = "Maria Lopez"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, url: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

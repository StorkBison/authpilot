import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """The directory holding data/. Next to the package in a checkout; on platforms that
    install the package into site-packages (e.g. Vercel), the working directory."""
    for candidate in (Path(__file__).resolve().parent.parent, Path.cwd()):
        if (candidate / "data" / "cases.json").exists():
            return candidate
    return Path.cwd()


ROOT = _project_root()


class Settings(BaseSettings):
    """Runtime configuration from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Serverless platforms (Vercel sets VERCEL=1): no background work after a response, a
    # read-only filesystem except /tmp, and many short-lived instances.
    serverless: bool = Field(default_factory=lambda: os.getenv("VERCEL") == "1")

    database_url: str = ""
    fhir_data_dir: Path = ROOT / "data" / "fhir"
    fhir_base_url: str | None = None

    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"
    agent_max_steps: int = Field(default=30, ge=5, le=100)

    payer_decision_after_checks: int = Field(default=1, ge=0, le=10)
    coordinator_name: str = "Maria Lopez"

    # Public deployments: cap agent runs per rolling hour (0 = unlimited) so a shared demo
    # with an API key cannot run up costs.
    max_agent_runs_per_hour: int = Field(default=0, ge=0)

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, url: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @model_validator(mode="after")
    def _default_database(self) -> "Settings":
        if not self.database_url:
            # /tmp is the only writable path on serverless; data there is per-instance.
            self.database_url = (
                "sqlite:////tmp/authpilot.db" if self.serverless else "sqlite:///./var/authpilot.db"
            )
        return self

    @property
    def ephemeral_database(self) -> bool:
        return self.serverless and self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()

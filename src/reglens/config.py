"""Application settings, read from environment variables and the `.env` file.

Every URL, key and threshold used by the code comes from here, so nothing is
hard-coded in the logic and tests can override values easily.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the configuration. Field names map to UPPER_CASE env vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vector database
    qdrant_url: str = "http://localhost:6333"

    # Queue + semantic cache
    redis_url: str = "redis://localhost:6379/0"

    # Table values
    postgres_user: str = "reglens"
    # SecretStr hides the value when the settings object is printed or logged.
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "reglens"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # LLM ("fake" = no API calls; used until Phase 8 and in tests)
    llm_provider: str = "fake"
    mistral_api_key: SecretStr = SecretStr("")

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (read the environment only once)."""
    return Settings()

"""Application settings, read from environment variables and the `.env` file.

Every URL, key and threshold used by the code comes from here, so nothing is
hard-coded in the logic and tests can override values easily.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the configuration. Field names map to UPPER_CASE env vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Corpus locations (relative to the repository root, where scripts are run)
    data_dir: Path = Path("data")
    synthetic_seed: int = 42

    @property
    def sources_file(self) -> Path:
        return self.data_dir / "sources.yaml"

    @property
    def raw_dir(self) -> Path:
        """Corpus files. Ingestion reads this folder and nothing else."""
        return self.data_dir / "raw"

    @property
    def ground_truth_dir(self) -> Path:
        """What the synthetic generator planted (PII, late incidents…). Never ingested,
        so it sits next to data/raw/, not inside it."""
        return self.data_dir / "ground_truth"

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

"""Application settings, read from environment variables and the `.env` file.

Every URL, key and threshold used by the code comes from here, so nothing is
hard-coded in the logic and tests can override values easily.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    @property
    def processed_dir(self) -> Path:
        """Derived artifacts (parsed documents, chunks). Always PII-masked."""
        return self.data_dir / "processed"

    @property
    def docling_cache_dir(self) -> Path:
        """Raw Docling output (unmasked, like data/raw/): never ingested, never committed."""
        return self.data_dir / "cache" / "docling"

    @property
    def results_dir(self) -> Path:
        """Committed experiment results (the evidence for every reported number)."""
        return Path("results")

    # PII masking (Presidio). spaCy model per language: decision O5 = md. lg was measured and
    # missed more names than md (results/2026-11-19_pii_names_ablation.json).
    pii_spacy_models: dict[str, str] = {"en": "en_core_web_md", "fr": "fr_core_news_md"}
    # Context recognizer: mask the name after "Owner:", "Deputy:", "Responsable :" ...
    pii_context_names: bool = True
    # Texts per spaCy nlp.pipe batch.
    pii_batch_size: int = 64
    # Minimum recognizer score to mask a span. Low on purpose: missing PII is worse than
    # masking a harmless word (false positives are counted and reported instead).
    pii_score_threshold: float = 0.35
    # Regions used to validate phone numbers (the synthetic corpus has UK and FR numbers).
    pii_phone_regions: list[str] = ["GB", "FR"]

    # Docling (PDF parsing). OCR is only needed for scanned PDFs; the bank reports are
    # born-digital, so it is off by default (measured: see results/EXPERIMENTS.md).
    docling_do_ocr: bool = False
    # "fast" TableFormer is ~2.2x quicker but changed 63 % of cells on the sample: not usable
    # with the 100 %-of-cells requirement.
    docling_table_mode: Literal["accurate", "fast"] = "accurate"
    # All cores: 8 threads were 34 % faster than Docling's default 4, identical output.
    docling_num_threads: int = os.cpu_count() or 4
    # Table cell text: True = PDF text cells matched into the predicted grid (Docling default).
    docling_cell_matching: bool = True

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

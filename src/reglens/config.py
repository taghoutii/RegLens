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

    @property
    def processed_dir(self) -> Path:
        """Derived artifacts (parsed documents, chunks). Always PII-masked."""
        return self.data_dir / "processed"

    @property
    def results_dir(self) -> Path:
        """Committed experiment results (the evidence for every reported number)."""
        return Path("results")

    # PII masking (Presidio). spaCy model per language: md. lg was measured on planted names
    # and missed more of them than md, at a larger size.
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

    # Embedding model: bge-m3 (FR + EN). Phase 3 uses its tokenizer (chunk sizes) and its
    # dense vectors (semantic cuts); Phase 5 indexes with it.
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = 32

    # Chunking (Phase 3). Starting values, kept until the Phase 6 retrieval comparison.
    chunk_max_tokens: int = 512
    chunk_min_tokens: int = 100  # a smaller piece is merged with its neighbour
    chunk_overlap_tokens: int = 64  # only on semantic cuts (and in the recursive strategy)
    # Semantic cut threshold. None = calibrate at each run: the `chunk_semantic_percentile`-th
    # percentile of consecutive-sentence similarity inside the pieces that need a cut. A
    # value (CHUNK_SEMANTIC_THRESHOLD) fixes it. The value used is written with the chunks
    # (data/processed/chunks/run.json).
    chunk_semantic_threshold: float | None = None
    chunk_semantic_percentile: float = 5.0
    # Log events that are noise for a question (left out of incident chunks).
    chunk_incident_noise_events: list[str] = ["monitoring"]

    # Vector database
    qdrant_url: str = "http://localhost:6333"

    # Queue, job status + semantic cache
    redis_url: str = "redis://localhost:6379/0"

    # LLM ("fake" = no API calls; used until Phase 8 and in tests).
    # SecretStr hides the value when the settings object is printed or logged.
    llm_provider: str = "fake"
    mistral_api_key: SecretStr = SecretStr("")

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (read the environment only once)."""
    return Settings()

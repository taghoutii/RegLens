"""Shared helpers: detect whether heavy local resources are available.

Integration tests (marker `integration`) call these and skip cleanly when a resource is
missing, so `pytest` passes on a machine with only `.[dev]` installed. CI also runs
`pytest -m "not integration"`.
"""

import importlib.util
import socket
from pathlib import Path

import pytest

from reglens.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def spacy_models_installed() -> bool:
    models = Settings(_env_file=None).pii_spacy_models.values()
    return all(importlib.util.find_spec(m) is not None for m in models)


def postgres_reachable() -> bool:
    s = Settings()
    try:
        with socket.create_connection((s.postgres_host, s.postgres_port), timeout=1):
            return True
    except OSError:
        return False


def corpus_present() -> bool:
    raw = REPO_ROOT / "data" / "raw"
    return any((raw / "regulations").glob("*.html")) and any((raw / "policies").glob("*.md"))


def require(condition: bool, reason: str) -> None:
    if not condition:
        pytest.skip(reason)

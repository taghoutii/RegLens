"""Shared helpers: detect whether heavy local resources are available.

Integration tests (marker `integration`) call these and skip cleanly when a resource is
missing, so `pytest` passes on a machine with only `.[dev]` installed. CI also runs
`pytest -m "not integration"`.
"""

import socket
from pathlib import Path

import pytest

from reglens.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def postgres_reachable() -> bool:
    s = Settings()
    try:
        with socket.create_connection((s.postgres_host, s.postgres_port), timeout=1):
            return True
    except OSError:
        return False


def require(condition: bool, reason: str) -> None:
    if not condition:
        pytest.skip(reason)

"""Shared helpers: detect whether heavy local resources are available.

Integration tests (marker `integration`) call these and skip cleanly when a resource is
missing, so `pytest` passes on a machine with only `.[dev]` installed. CI also runs
`pytest -m "not integration"`.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, reason: str) -> None:
    if not condition:
        pytest.skip(reason)

"""Tests for reading data/sources.yaml."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from reglens.corpus.sources import Source, download_targets, load_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_real_sources_file_is_valid() -> None:
    corpus = load_corpus(REPO_ROOT / "data" / "sources.yaml")
    assert len(corpus.regulations) == 8
    assert len(corpus.reports) == 6


def test_targets_one_file_per_language(tmp_path: Path) -> None:
    corpus = load_corpus(REPO_ROOT / "data" / "sources.yaml")
    targets = download_targets(corpus, tmp_path)
    # 8 regulations x 2 languages + 6 reports + 1 spreadsheet appendix (ING); generated
    # sources are not downloaded
    assert len(targets) == 8 * 2 + 6 + 1
    paths = {t.path.relative_to(tmp_path).as_posix() for t in targets}
    assert "regulations/dora_fr.html" in paths
    assert "reports/ing_group_pillar3_2025.pdf" in paths
    assert "reports/ing_group_pillar3_2025.xlsx" in paths
    assert len(paths) == len(targets)  # no two targets write the same file


def test_auto_source_without_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Source(id="x", what="y", format="pdf", mode="auto")


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Source.model_validate(
            {"id": "x", "what": "y", "format": "pdf", "mode": "generated", "typo": 1}
        )

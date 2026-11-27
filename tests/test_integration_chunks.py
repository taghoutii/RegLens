"""Checks on the chunks actually written (run after scripts/chunk_corpus.py).

- Required test 1 on the real corpus: every main-strategy chunk is a slice of ONE
  parsed unit (so it never crosses an article), and every metadata field is filled;
- chunk ids are unique inside each strategy;
- every provider of the register ground truth has exactly one chunk in every strategy.
"""

import json
from collections import Counter, defaultdict

import pytest

from reglens.ingestion.chunking import STRATEGIES, Chunk
from reglens.ingestion.models import ParsedDocument
from tests.chunk_stubs import required_fields_filled
from tests.conftest import REPO_ROOT, require

pytestmark = pytest.mark.integration

CHUNKS = REPO_ROOT / "data" / "processed" / "chunks"
PARSED = REPO_ROOT / "data" / "processed" / "parsed"


def _chunks(strategy: str) -> list[Chunk]:
    files = sorted((CHUNKS / strategy).glob("*.jsonl"))
    require(bool(files), f"no {strategy} chunks yet (scripts/chunk_corpus.py)")
    return [
        Chunk.model_validate_json(line)
        for f in files
        for line in f.read_text(encoding="utf-8").splitlines()
    ]


def test_required_1_on_the_corpus() -> None:
    chunks = _chunks("structure_semantic")
    units: dict[tuple[str, str], str] = defaultdict(str)
    for f in PARSED.glob("*.json"):
        doc = ParsedDocument.model_validate_json(f.read_text(encoding="utf-8"))
        by_unit: dict[str, list[str]] = defaultdict(list)
        for b in doc.blocks:
            if b.kind in ("title", "text"):
                by_unit[b.parent_id].append(b.text)
        for parent_id, texts in by_unit.items():
            units[(doc.langue, parent_id)] = "\n".join(texts)
    crossing = [c.chunk_id for c in chunks
                if c.kind == "text" and c.text not in units[(c.langue, c.parent_id)]]  # fmt: skip
    assert crossing == []
    unfilled = [c.chunk_id for c in chunks if not required_fields_filled(c)]
    assert unfilled == []


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_chunk_ids_are_unique(strategy: str) -> None:
    ids = [c.chunk_id for c in _chunks(strategy)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_one_chunk_per_provider(strategy: str) -> None:
    truth = REPO_ROOT / "data" / "ground_truth" / "register.json"
    require(truth.exists(), "register not generated yet (scripts/generate_synthetic.py)")
    providers = {p["provider_id"] for p in json.loads(truth.read_text(encoding="utf-8"))}
    per_unit = Counter(c.parent_id for c in _chunks(strategy) if c.kind == "provider")
    assert per_unit == Counter({f"register#{p}": 1 for p in providers})


def test_run_records_the_threshold_used() -> None:
    run = CHUNKS / "run.json"
    require(run.exists(), "no chunking run yet (scripts/chunk_corpus.py)")
    record = json.loads(run.read_text(encoding="utf-8"))
    assert isinstance(record["threshold"], float)
    calibrated = record["calibration"].get("threshold")
    assert calibrated is None or calibrated == record["threshold"]  # None: fixed in config
    # every strategy folder on disk says when and with which threshold it was built
    built = record["strategies"]
    for strategy in STRATEGIES:
        if any((CHUNKS / strategy).glob("*.jsonl")):
            assert isinstance(built[strategy]["threshold"], float)

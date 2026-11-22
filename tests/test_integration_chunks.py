"""Checks on the chunks actually written (run after scripts/chunk_corpus.py).

- Required test 1 on the real corpus: every main-strategy chunk is a slice of ONE
  parsed unit (so it never crosses an article), and every metadata field is filled;
- every table chunk's parent_id is a table_id in PostgreSQL (chunk -> exact values);
- no planted PII/secret value in any chunk file;
- chunk ids are unique inside each strategy.
"""

from collections import defaultdict

import psycopg
import pytest

from reglens.config import Settings
from reglens.evaluation.pii_leaks import load_planted
from reglens.ingestion.chunking import STRATEGIES, Chunk
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.table_store import conninfo
from tests.conftest import REPO_ROOT, postgres_reachable, require
from tests.test_chunking import required_fields_filled

pytestmark = pytest.mark.integration

CHUNKS = REPO_ROOT / "data" / "processed" / "chunks"
PARSED = REPO_ROOT / "data" / "processed" / "parsed"
TRUTH = REPO_ROOT / "data" / "ground_truth"


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


def test_table_chunks_point_to_postgres_rows() -> None:
    require(postgres_reachable(), "PostgreSQL not running (docker compose up -d)")
    tables = {c.parent_id for c in _chunks("structure_semantic") if c.kind == "table"}
    with psycopg.connect(conninfo(Settings())) as conn:
        stored = {r[0] for r in conn.execute("SELECT table_id FROM report_tables").fetchall()}
    assert tables and tables <= stored


def test_no_planted_value_in_chunks() -> None:
    require((TRUTH / "policies.json").exists(), "ground truth missing")
    values = [p.value for p in load_planted(TRUTH)]
    files = list(CHUNKS.rglob("*.jsonl"))
    require(bool(files), "no chunks yet")
    # pydantic writes non-ASCII characters as they are (no \u escapes), so a plain search works
    leaks = [
        f.relative_to(REPO_ROOT).as_posix()
        for f in files
        if any(v in f.read_text(encoding="utf-8") for v in values)
    ]
    assert leaks == []  # file names only

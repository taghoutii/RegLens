"""Acceptance check on what was actually written: 0 planted PII/secret values in
data/processed/ (parsed files and chunks; run after scripts/build_corpus.py).

Two checks:
  1. per document: the masked policies, log and register go through the leak checker
     (full values and leftover name parts, in the document they were planted in);
  2. everywhere: no planted *full* value appears in any processed file (catches a value
     copied into another document, e.g. a regulation).
Expected counts come from the ground truth and sources.yaml, never from fixed numbers.
"""

import json
from pathlib import Path

import pytest

from reglens.corpus.sources import load_corpus
from reglens.evaluation.pii_leaks import check_leaks, load_planted
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_synthetic import LOG_DOC_ID, REGISTER_DOC_ID
from tests.conftest import REPO_ROOT, require

pytestmark = pytest.mark.integration

PROCESSED = REPO_ROOT / "data" / "processed"
PARSED = PROCESSED / "parsed"
TRUTH = REPO_ROOT / "data" / "ground_truth"


def _planted_values() -> list[str]:
    return [p.value for p in load_planted(TRUTH)]


def _expected_synthetic() -> set[str]:
    """Parsed-file stems of the synthetic documents, from the ground truth and sources.yaml."""
    corpus = load_corpus(REPO_ROOT / "data" / "sources.yaml")
    names: set[str] = set()
    if corpus.policies:
        policies = json.loads((TRUTH / "policies.json").read_text(encoding="utf-8"))
        names.update(f"{p['policy_id']}__{p['language']}" for p in policies)
    if corpus.logs:
        names.add(f"{LOG_DOC_ID}__en")
    if corpus.register_of_information:
        names.add(f"{REGISTER_DOC_ID}__en")
    return names


def test_processed_synthetic_documents_have_no_leak() -> None:
    require((TRUTH / "policies.json").exists(), "ground truth missing")
    files = sorted(PARSED.glob("POL-*.json"))
    files += sorted(PARSED.glob(f"{LOG_DOC_ID}__*.json"))
    files += sorted(PARSED.glob(f"{REGISTER_DOC_ID}__*.json"))
    require(bool(files), "synthetic documents not parsed yet (scripts/parse_corpus.py)")
    assert {f.stem for f in files} == _expected_synthetic()
    docs = {}
    for f in files:
        doc = ParsedDocument.model_validate_json(f.read_text(encoding="utf-8"))
        assert doc.masked, f.name
        docs[doc.doc_id] = doc
    planted = load_planted(TRUTH)
    report = check_leaks(docs, planted)
    assert sum(report.total.values()) == len(planted)  # every planted value was checked
    assert report.failures == [], report.summary()["failures"]  # ids only, never values


def test_no_planted_value_in_any_processed_file() -> None:
    # parsed documents (.json) and chunks (.jsonl); pydantic writes non-ASCII characters
    # as they are (no \u escapes), so a plain search works
    files = [*PROCESSED.rglob("*.json"), *PROCESSED.rglob("*.jsonl")]
    require(bool(files), "nothing in data/processed/ yet")
    require((TRUTH / "policies.json").exists(), "ground truth missing")
    values = _planted_values()
    leaks = [
        f.relative_to(REPO_ROOT).as_posix()
        for f in files
        if any(v in f.read_text(encoding="utf-8") for v in values)
    ]
    assert leaks == []  # file names only


def test_processed_files_are_all_masked() -> None:
    files = list(PARSED.glob("*.json"))
    require(bool(files), "nothing parsed yet")
    unmasked = [f.name for f in files if not _is_masked(f)]
    assert unmasked == []


def _is_masked(path: Path) -> bool:
    return ParsedDocument.model_validate_json(path.read_text(encoding="utf-8")).masked


def test_provider_names_survive_masking() -> None:
    """Company names are not personal data: each provider keeps its name in the register."""
    parsed, truth = PARSED / f"{REGISTER_DOC_ID}__en.json", TRUTH / "register.json"
    require(parsed.exists() and truth.exists(), "no parsed register yet")
    doc = ParsedDocument.model_validate_json(parsed.read_text(encoding="utf-8"))
    providers = json.loads(truth.read_text(encoding="utf-8"))
    by_id = {b.parent_id: b.text for b in doc.blocks}
    missing = [
        p["provider_id"]
        for p in providers
        if f"Provider name: {p['name']} |" not in by_id[f"{REGISTER_DOC_ID}#{p['provider_id']}"]
    ]
    assert missing == []

"""Acceptance check on the real synthetic corpus: 0 planted PII/secret values survive masking.

Needs the spaCy models (pip install -e ".[dev,models]"), data/raw/ and data/ground_truth/.
"""

import pytest

from reglens.config import Settings
from reglens.evaluation.pii_leaks import check_leaks, load_planted
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_synthetic import parse_incident_log, parse_policy
from reglens.ingestion.pii import PiiMasker
from tests.conftest import REPO_ROOT, corpus_present, require, spacy_models_installed

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def masker() -> PiiMasker:
    require(spacy_models_installed(), "spaCy models not installed (.[models])")
    return PiiMasker(Settings(_env_file=None))


def test_no_planted_value_survives_masking(masker: PiiMasker) -> None:
    require(corpus_present(), "corpus not generated (scripts/generate_synthetic.py)")
    raw, truth = REPO_ROOT / "data" / "raw", REPO_ROOT / "data" / "ground_truth"
    require((truth / "policies.json").exists(), "ground truth missing")
    docs: dict[str, ParsedDocument] = {}
    for f in sorted((raw / "policies").glob("POL-*.md")):
        d = masker.mask_document(parse_policy(f, source=f.name))
        docs[d.doc_id] = d
    log = raw / "logs" / "incident_log.jsonl"
    docs["incident_log"] = masker.mask_document(parse_incident_log(log, source=log.name))

    report = check_leaks(docs, load_planted(truth))
    assert sum(report.total.values()) == 750  # 350 in policies + 400 in the log
    # failures hold doc ids and kinds only, so the assertion message never prints a value
    assert report.failures == [], report.summary()["failures"]


def test_masking_marks_the_document(masker: PiiMasker) -> None:
    require(corpus_present(), "corpus not generated")
    path = next((REPO_ROOT / "data" / "raw" / "policies").glob("POL-001_*.md"))
    doc = masker.mask_document(parse_policy(path, source=path.name))
    assert doc.masked and doc.pii_counts.get("API_KEY") == 1

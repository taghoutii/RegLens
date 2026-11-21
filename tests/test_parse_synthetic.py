"""Policy and incident-log readers, on a small freshly generated corpus."""

import json
from pathlib import Path

from reglens.corpus.synthetic import generate_all
from reglens.ingestion.parse_synthetic import parse_incident_log, parse_policy


def _generate(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    generate_all(raw, tmp_path / "gt", seed=3, n_policies=8, n_incidents=12)
    return raw


def test_policy_sections_become_units(tmp_path: Path) -> None:
    raw = _generate(tmp_path)
    path = next((raw / "policies").glob("POL-001_*.md"))
    doc = parse_policy(path, source="x")
    assert doc.doc_id == "POL-001" and doc.type_doc == "policies"
    ids = [b.parent_id for b in doc.blocks]
    assert ids[0] == "POL-001#s0" and ids[1:] == [f"POL-001#s{n}" for n in range(1, len(ids))]
    header = doc.blocks[0]
    assert header.kind == "title" and "(BU-" in header.text  # unit code: the link to logs
    assert doc.blocks[1].section is not None


def test_policy_language_comes_from_the_file_name(tmp_path: Path) -> None:
    raw = _generate(tmp_path)
    for path in (raw / "policies").glob("POL-*.md"):
        assert parse_policy(path, source="x").langue == path.stem.rsplit("_", 1)[1]


def test_one_block_per_incident_with_all_events(tmp_path: Path) -> None:
    raw = _generate(tmp_path)
    log = raw / "logs" / "incident_log.jsonl"
    doc = parse_incident_log(log, source="x")
    n_lines = len(log.read_text(encoding="utf-8").splitlines())
    assert doc.doc_id == "incident_log"
    assert len(doc.blocks) == 12
    assert sum(len(b.records or []) for b in doc.blocks) == n_lines
    block = doc.blocks[0]
    assert block.parent_id == "incident_log#INC-0001" and block.kind == "records"
    stamps = [e["timestamp"] for e in block.records or []]
    assert stamps == sorted(stamps)
    assert all(json.dumps(e) for e in block.records or [])

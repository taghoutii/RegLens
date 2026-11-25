"""The corpus is exactly what sources.yaml and the generator produce: outputs of documents
no longer listed are pruned, and a partial run never deletes another type's outputs."""

from pathlib import Path

from reglens.corpus.sources import Corpus
from reglens.ingestion.inventory import expected_documents, prune, report_ids, stale_files

CORPUS = Corpus.model_validate({
    "regulations": [{"id": "dora", "short_name": "DORA", "what": "x", "format": "html",
                     "mode": "auto", "urls": {"en": "https://e.eu/a", "fr": "https://e.eu/b"}}],
    "reports": [{"id": "ing", "what": "x", "format": "pdf", "mode": "auto",
                 "url": "https://ing.com/r.pdf", "language": "en"}],
    "policies": [{"id": "synthetic_policies", "what": "x", "format": "md", "mode": "generated"}],
    "logs": [{"id": "synthetic_incident_logs", "what": "x", "format": "jsonl",
              "mode": "generated"}],
})  # fmt: skip


def _raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "policies").mkdir(parents=True)
    (raw / "logs").mkdir()
    (raw / "policies" / "POL-001_ict_incident_en.md").write_text("# x", encoding="utf-8")
    (raw / "policies" / "POL-002_ict_incident_fr.md").write_text("# x", encoding="utf-8")
    (raw / "logs" / "incident_log.jsonl").write_text("", encoding="utf-8")
    # a raw file of a document no longer listed is not part of the corpus
    (raw / "regulations").mkdir()
    (raw / "regulations" / "removed_act_en.html").write_text("", encoding="utf-8")
    return raw


def test_expected_documents_follow_sources_and_generated_files(tmp_path: Path) -> None:
    assert expected_documents(CORPUS, _raw(tmp_path)) == {
        "dora__en", "dora__fr", "ing__en", "POL-001__en", "POL-002__fr", "incident_log__en",
    }  # fmt: skip
    assert report_ids(CORPUS) == {"ing"}


def test_prune_deletes_only_unlisted_documents(tmp_path: Path) -> None:
    expected = expected_documents(CORPUS, _raw(tmp_path))
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    for stem in [*expected, "removed_act__en", "old_bank_report__en"]:
        (parsed / f"{stem}.json").write_text("{}", encoding="utf-8")
    assert [p.name for p in stale_files(parsed, "*.json", expected)] == [
        "old_bank_report__en.json", "removed_act__en.json",
    ]  # fmt: skip
    removed = prune(parsed, "*.json", expected)
    assert {p.stem for p in removed} == {"removed_act__en", "old_bank_report__en"}
    assert {p.stem for p in parsed.glob("*.json")} == expected  # listed documents untouched


def test_prune_of_a_missing_folder_is_a_no_op(tmp_path: Path) -> None:
    assert prune(tmp_path / "absent", "*.json", {"dora__en"}) == []


def test_unlisted_generated_type_is_not_expected(tmp_path: Path) -> None:
    corpus = CORPUS.model_copy(update={"policies": [], "logs": []})
    assert expected_documents(corpus, _raw(tmp_path)) == {"dora__en", "dora__fr", "ing__en"}

"""The documents the corpus consists of, and pruning of outputs that no longer belong to it.

The corpus is exactly what data/sources.yaml lists and the synthetic generator wrote:
    regulations  one document per listed language    "dora__en", "dora__fr"
    reports      one document per listed report       "ing_group_pillar3_2025__en"
    policies     one document per generated POL file  "POL-005__en"
    logs         the generated incident log           "incident_log__en"
Names are the parsed-file stems (`ParsedDocument.output_name` without ".json"); chunk
files use the same stem. A document removed from sources.yaml keeps no derived output:
the parse step deletes its parsed file and PostgreSQL tables, and the chunk step never
reads it.

The list is computed from sources.yaml and data/raw/, not from what a run wrote, so a
partial run (`parse_corpus.py --only regulations`) never deletes another type's outputs.
"""

from pathlib import Path

from reglens.corpus.sources import Corpus
from reglens.ingestion.parse_synthetic import LOG_DOC_ID, POLICY_FILE


def expected_documents(corpus: Corpus, raw_dir: Path) -> set[str]:
    """Stems `<doc_id>__<langue>` of every document of the corpus."""
    names: set[str] = set()
    for src in corpus.regulations:
        names.update(f"{src.id}__{lang}" for lang in (src.urls or {}))
    for src in corpus.reports:
        names.add(f"{src.id}__{src.language or 'en'}")
    if corpus.policies:
        for path in sorted((raw_dir / "policies").glob("POL-*.md")):
            match = POLICY_FILE.match(path.name)
            if match:
                names.add(f"{match.group(1)}__{match.group(2)}")
    if corpus.logs and (raw_dir / "logs" / "incident_log.jsonl").exists():
        names.add(f"{LOG_DOC_ID}__en")
    return names


def report_ids(corpus: Corpus) -> set[str]:
    """doc_ids whose tables belong in PostgreSQL."""
    return {src.id for src in corpus.reports}


def stale_files(folder: Path, pattern: str, expected: set[str]) -> list[Path]:
    """Files of `folder` matching `pattern` whose stem is not an expected document."""
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob(pattern) if p.stem not in expected)


def prune(folder: Path, pattern: str, expected: set[str]) -> list[Path]:
    """Delete the stale files of `folder`; return what was deleted."""
    removed = stale_files(folder, pattern, expected)
    for path in removed:
        path.unlink()
    return removed

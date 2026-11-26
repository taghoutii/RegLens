"""Parse and mask the corpus: data/raw/ -> data/processed/parsed/.

Usage (repository root, virtual environment active):
    python scripts/parse_corpus.py                       # everything
    python scripts/parse_corpus.py --only regulations    # one or several document types

Order per document: parse -> mask -> write. Nothing unmasked is ever written to
data/processed/. Only the documents of the corpus are parsed (sources.yaml + generated
files); parsed files of documents no longer listed are deleted
(reglens/ingestion/inventory.py).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.ingestion.inventory import expected_documents, prune
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_eurlex import parse_eurlex
from reglens.ingestion.parse_synthetic import (
    REGISTER_FILE,
    parse_incident_log,
    parse_policy,
    parse_register,
)
from reglens.ingestion.pii import PiiMasker

TYPES = ["regulations", "policies", "logs", "register"]
logger = logging.getLogger("parse_corpus")


def write(doc: ParsedDocument, out_dir: Path) -> Path:
    if not doc.masked:
        raise ValueError(f"{doc.doc_id}: refusing to write an unmasked document")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / doc.output_name
    path.write_text(doc.model_dump_json(indent=1), encoding="utf-8")
    return path


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", choices=TYPES, default=TYPES)
    args = parser.parse_args()
    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(message)s")

    raw, out_dir = settings.raw_dir, settings.processed_dir / "parsed"
    corpus = load_corpus(settings.sources_file)
    masker = PiiMasker(settings)
    for path in prune(out_dir, "*.json", expected_documents(corpus, raw)):
        logger.info("%s: not in the corpus, parsed file deleted", path.name)

    def done(doc: ParsedDocument, t0: float) -> None:
        masked = masker.mask_document(doc)
        path = write(masked, out_dir)
        logger.info("%-40s %4d blocks, %5.1fs -> %s", masked.doc_id, len(masked.blocks),
                    time.perf_counter() - t0, path.name)  # fmt: skip

    if "regulations" in args.only:
        for src in corpus.regulations:
            for lang in ("en", "fr"):
                path = raw / "regulations" / f"{src.id}_{lang}.html"
                t0 = time.perf_counter()
                doc = parse_eurlex(
                    path, doc_id=src.id, langue=lang, reglement=src.short_name,
                    source=path.as_posix(),
                )  # fmt: skip
                done(doc, t0)
    if "policies" in args.only and corpus.policies:
        for path in sorted((raw / "policies").glob("POL-*.md")):
            t0 = time.perf_counter()
            done(parse_policy(path, source=path.as_posix()), t0)
    if "logs" in args.only and corpus.logs:
        path = raw / "logs" / "incident_log.jsonl"
        t0 = time.perf_counter()
        done(parse_incident_log(path, source=path.as_posix()), t0)
    if "register" in args.only and corpus.register_of_information:
        path = raw / "register" / REGISTER_FILE
        t0 = time.perf_counter()
        done(parse_register(path, source=path.as_posix()), t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())

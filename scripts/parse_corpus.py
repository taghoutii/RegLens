"""Parse and mask the corpus: data/raw/ -> data/processed/parsed/ (+ report tables -> PostgreSQL).

Usage (repository root, virtual environment active, `docker compose up -d` for tables):
    python scripts/parse_corpus.py --only regulations
    python scripts/parse_corpus.py --only reports --max-pages 10     # Docling sample, no cache
    python scripts/parse_corpus.py --only reports                    # from data/cache/docling/
    python scripts/parse_corpus.py                                   # everything
    python scripts/parse_corpus.py --no-db                           # skip PostgreSQL

Order per document: parse -> mask -> write. Nothing unmasked is ever written to
data/processed/ or PostgreSQL. Full reports are read from the Docling cache, which
scripts/run_docling.py fills (hours on CPU). A report with a spreadsheet appendix
(sources.yaml `appendix`) has the tables it matches with certainty replaced by their
spreadsheet version before masking (reglens/ingestion/structured_tables.py).

Only the documents of the corpus are parsed (sources.yaml + generated files); parsed
files and PostgreSQL tables of documents no longer listed are deleted
(reglens/ingestion/inventory.py).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import psycopg
from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.ingestion.inventory import expected_documents, prune, report_ids
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_eurlex import parse_eurlex
from reglens.ingestion.parse_pdf import (
    cache_paths,
    docling_config,
    from_docling,
    is_cached,
    make_converter,
    parse_pdf,
)
from reglens.ingestion.parse_synthetic import parse_incident_log, parse_policy
from reglens.ingestion.pii import PiiMasker
from reglens.ingestion.structured_tables import apply_spreadsheet, summary
from reglens.ingestion.table_store import (
    conninfo,
    delete_other_documents,
    ensure_schema,
    store_tables,
)

TYPES = ["regulations", "reports", "policies", "logs"]
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
    parser.add_argument("--max-pages", type=int, help="parse only the first N pages of each PDF")
    parser.add_argument("--no-db", action="store_true", help="do not write tables to PostgreSQL")
    args = parser.parse_args()
    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(message)s")

    raw, out_dir = settings.raw_dir, settings.processed_dir / "parsed"
    max_header = settings.table_max_inferred_header_rows
    corpus = load_corpus(settings.sources_file)
    masker = PiiMasker(settings)
    conn = None if args.no_db or "reports" not in args.only else psycopg.connect(conninfo(settings))
    if conn is not None:
        ensure_schema(conn)
        for doc_id in delete_other_documents(conn, report_ids(corpus)):
            logger.info("%s: not in sources.yaml, its tables deleted from PostgreSQL", doc_id)
    expected = expected_documents(corpus, raw)
    for path in prune(out_dir, "*.json", expected):
        logger.info("%s: not in the corpus, parsed file deleted", path.name)

    def done(doc: ParsedDocument, t0: float) -> None:
        masked = masker.mask_document(doc)
        path = write(masked, out_dir)
        extra = ""
        if conn is not None and masked.type_doc == "reports":
            extra = f", {store_tables(conn, masked)} cells -> PostgreSQL"
        logger.info("%-40s %4d blocks, %5.1fs%s -> %s", masked.doc_id, len(masked.blocks),
                    time.perf_counter() - t0, extra, path.name)  # fmt: skip

    try:
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
        if "reports" in args.only:
            # Full reports come from the Docling cache (scripts/run_docling.py); --max-pages
            # converts a sample directly, without caching it.
            converter = None
            for src in corpus.reports:
                path = raw / "reports" / f"{src.id}.pdf"
                lang = src.language or "en"
                t0 = time.perf_counter()
                if args.max_pages:
                    converter = converter or make_converter(
                        do_ocr=settings.docling_do_ocr,
                        table_mode=settings.docling_table_mode,
                        num_threads=settings.docling_num_threads,
                        do_cell_matching=settings.docling_cell_matching,
                    )
                    pages = (1, args.max_pages)
                    doc = parse_pdf(path, converter, doc_id=src.id, langue=lang,
                                    source=path.as_posix(), page_range=pages,
                                    max_header_rows=max_header)  # fmt: skip
                else:
                    doc_file = cache_paths(settings.docling_cache_dir, src.id)[0]
                    cache_ok = is_cached(settings.docling_cache_dir, src.id,
                                         docling_config(settings))  # fmt: skip
                    if not cache_ok:  # missing, or made with other Docling settings
                        logger.warning("%s: not in the Docling cache with the current "
                                       "settings: run scripts/run_docling.py", src.id)  # fmt: skip
                        continue
                    docling_doc = DoclingDocument.load_from_json(doc_file)
                    doc = from_docling(docling_doc, doc_id=src.id, langue=lang,
                                       source=path.as_posix(),
                                       max_header_rows=max_header)  # fmt: skip
                appendix = (
                    raw / "reports" / f"{src.id}.{src.appendix.format}" if src.appendix else None
                )
                if appendix is not None and appendix.exists():
                    replaced = apply_spreadsheet(doc, appendix,
                                                 min_numbers=settings.spreadsheet_min_numbers,
                                                 min_share=settings.spreadsheet_min_share,
                                                 margin=settings.spreadsheet_margin)  # fmt: skip
                    logger.info("%s: spreadsheet appendix %s", src.id, summary(replaced))
                done(doc, t0)
        if "policies" in args.only:
            for path in sorted((raw / "policies").glob("POL-*.md")):
                t0 = time.perf_counter()
                done(parse_policy(path, source=path.as_posix()), t0)
        if "logs" in args.only:
            path = raw / "logs" / "incident_log.jsonl"
            t0 = time.perf_counter()
            done(parse_incident_log(path, source=path.as_posix()), t0)
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run Docling on every report once and cache its output (long: hours on CPU).

Usage (repository root, virtual environment active):
    python scripts/run_docling.py            # converts the reports not yet in the cache
    python scripts/run_docling.py --status   # what is cached, nothing converted
    python scripts/run_docling.py --record   # also write results/ once every report is cached

Resumable per document: a report whose JSON is already in data/cache/docling/ is skipped,
so after an interruption just run the same command again. A failing report is logged and
the others continue. With --record, once every report is cached, writes
results/<date>_docling_full_run.json (pages, seconds per page for each report).

The cache holds unmasked text (same status as data/raw/): it is never committed and never
ingested. scripts/parse_corpus.py reads it, masks, and writes data/processed/.
"""

import argparse
import json
import logging
import sys
import time
from datetime import date

import pypdfium2 as pdfium

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.evaluation.results import write_result
from reglens.ingestion.parse_pdf import (
    cache_paths,
    cached_conversion,
    docling_config,
    is_cached,
    make_converter,
)

logger = logging.getLogger("run_docling")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true", help="only show what is cached")
    parser.add_argument("--record", action="store_true", help="write the run to results/")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s"
    )
    for noisy in ("docling", "docling_core", "docling_ibm_models", "transformers", "torch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cache_dir = settings.docling_cache_dir
    config = docling_config(settings)
    reports = load_corpus(settings.sources_file).reports
    todo = []
    for src in reports:
        pdf = settings.raw_dir / "reports" / f"{src.id}.pdf"
        cached = is_cached(cache_dir, src.id, config)  # same report, same settings
        pages = len(pdfium.PdfDocument(pdf))
        logger.info("%-34s %4d pages  %s", src.id, pages, "cached" if cached else "to do")
        if not cached:
            todo.append((src.id, pdf, pages))
    if args.status:
        return 0

    failed: list[str] = []
    todo.sort(key=lambda t: t[2])  # smallest first: finished reports are usable sooner
    if todo:
        converter = make_converter(
            do_ocr=settings.docling_do_ocr,
            table_mode=settings.docling_table_mode,
            num_threads=settings.docling_num_threads,
            do_cell_matching=settings.docling_cell_matching,
        )
        for doc_id, pdf, pages in todo:
            logger.info("converting %s (%d pages)...", doc_id, pages)
            t0 = time.perf_counter()
            try:
                cached_conversion(pdf, converter, cache_dir, doc_id, config)
            except Exception:  # one failing report must not stop the others
                logger.exception("failed: %s", doc_id)
                failed.append(doc_id)
                continue
            s = time.perf_counter() - t0
            logger.info("done %s: %.0f s, %.1f s/page", doc_id, s, s / pages)

    metas = []
    for src in reports:
        meta_file = cache_paths(cache_dir, src.id)[1]
        if is_cached(cache_dir, src.id, config):
            metas.append(json.loads(meta_file.read_text(encoding="utf-8")))
    if failed or len(metas) < len(reports):
        logger.warning("not complete (failed: %s): no result written; rerun to resume", failed)
        return 1
    if not args.record:
        logger.info("all %d reports cached", len(metas))
        return 0

    for m in metas:
        m["s_per_page"] = round(m["convert_s"] / m["pages"], 1)
    pages = sum(m["pages"] for m in metas)
    seconds = sum(m["convert_s"] for m in metas)
    hours, s_per_page = round(seconds / 3600, 2), round(seconds / pages, 1)
    result = {
        "name": "docling_full_run",
        "date": date.today().isoformat(),
        "config": {
            "do_ocr": settings.docling_do_ocr,
            "table_mode": settings.docling_table_mode,
            "num_threads": settings.docling_num_threads,
        },
        "documents": metas,
        "total": {"pages": pages, "convert_h": hours, "s_per_page": s_per_page},
    }
    out = write_result(settings.results_dir, result)
    print(f"{pages} pages in {hours} h, {s_per_page} s/page")
    logger.info("written: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

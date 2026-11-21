"""Compare Docling's two ways of filling table cells, on the same 20 tables.

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/compare_cell_matching.py               # 20 tables over the cached reports

Variants:
    cell_matching=True   (default) the PDF's own text cells are placed into the grid that
                         TableFormer predicts
    cell_matching=False  the text comes with TableFormer's predicted cells directly

The 20 tables are chosen from the cache exactly like scripts/check_tables.py (same seed,
spread over the reports). Both variants are converted again on the pages of those tables
only, so they are compared under the same conditions. A variant's table is the one on the
same page that overlaps the chosen table most. Each table is checked against the PDF text
layer; misses are counted by cell kind, plus the low-confidence flag (several numbers in
one cell). Writes results/<date>_table_cell_matching.json (ids and counts only).
"""

import argparse
import logging
import sys
import time
import warnings
from datetime import date
from typing import Any

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.evaluation.results import append_row, write_result
from reglens.evaluation.table_check import (
    TableCheck,
    check_table,
    matching_table,
    pick_tables,
    summarize,
    table_row,
)
from reglens.ingestion.parse_pdf import cache_paths, convert_pdf, make_converter


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-record", action="store_true", help="smoke test: write nothing")
    args = parser.parse_args()
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.ERROR)

    reports = load_corpus(settings.sources_file).reports
    cached: dict[str, DoclingDocument] = {}
    pdfs: dict[str, pdfium.PdfDocument] = {}
    paths = {}
    for src in reports:
        doc_file = cache_paths(settings.docling_cache_dir, src.id)[0]
        if doc_file.exists():
            cached[src.id] = DoclingDocument.load_from_json(doc_file)
            paths[src.id] = settings.raw_dir / "reports" / f"{src.id}.pdf"
            pdfs[src.id] = pdfium.PdfDocument(paths[src.id])
    sample = pick_tables(cached, args.n, args.seed)
    print(f"{len(sample)} tables from {len(cached)} cached reports")

    variants: dict[str, dict[str, Any]] = {}
    for cell_matching in (True, False):
        name = f"cell_matching={cell_matching}"
        converter = make_converter(
            do_ocr=settings.docling_do_ocr,
            table_mode=settings.docling_table_mode,
            num_threads=settings.docling_num_threads,
            do_cell_matching=cell_matching,
        )
        checks: list[TableCheck] = []
        pages_done, seconds, not_found = 0, 0.0, []
        for doc_id, index, table in sample:
            pages = sorted({p.page_no for p in table.prov})
            t0 = time.perf_counter()
            doc, _ = convert_pdf(paths[doc_id], converter, (pages[0], pages[-1]))
            seconds += time.perf_counter() - t0
            pages_done += pages[-1] - pages[0] + 1
            found = matching_table(table, doc)
            if found is None:  # the variant did not produce this table: every cell missing
                not_found.append(f"{doc_id}#tbl-{index}")
                miss = check_table(pdfs[doc_id], cached[doc_id], table, doc_id, index)
                miss.found, miss.missing_by_kind = 0, miss.cells_by_kind.copy()
                miss.missing = [(-1, -1)] * miss.cells
                checks.append(miss)
                continue
            checks.append(check_table(pdfs[doc_id], doc, found, doc_id, index))
        total = summarize(checks)
        variants[name] = {
            "total": total,
            "tables_not_found": not_found,
            "convert_s_per_page": round(seconds / pages_done, 1),
            "tables": [table_row(c) for c in checks],
        }
        print(f"{name}: {total} ; {seconds / pages_done:.1f} s/page ; not found {not_found}")

    result = {
        "name": "table_cell_matching",
        "date": date.today().isoformat(),
        "method": "same tables, both variants re-converted on their pages; cells checked "
        "against the pypdfium2 text layer inside the table box",
        "seed": args.seed,
        "documents": sorted(cached),
        "sample": [f"{d}#tbl-{i}" for d, i, _ in sample],
        "variants": variants,
    }
    if args.no_record:
        return 0
    out = write_result(settings.results_dir, result)
    key = "; ".join(
        f"{n}: {v['total']['found']}/{v['total']['cells']} ({v['total']['rate']}), missing "
        + ", ".join(f"{k} {x['missing']}/{x['cells']}" for k, x in v["total"]["by_kind"].items())
        + f", flag caught {v['total']['missing_caught_by_flag']}"
        for n, v in variants.items()
    )
    append_row(
        settings.results_dir,
        f"| {result['date']} | table_cell_matching | 2 | Docling do_cell_matching True vs "
        f"False, {len(sample)} tables over {len(cached)} reports | {key} | "
        f"`{out.as_posix()}` |",
    )
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

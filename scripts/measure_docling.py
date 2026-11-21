"""Measure Docling speed on CPU and the effect of its settings on table cells.

Usage (repository root, virtual environment active):
    python scripts/measure_docling.py                       # default sample: 5 table pages
    python scripts/measure_docling.py --pdf <path> --pages 5 9

Runs each variant (TableFormer mode x threads, OCR off) on the same pages, times model
loading and conversion separately, and compares every table cell with the first variant
(the reference). Writes results/<date>_docling_speed.json and one row in EXPERIMENTS.md.
"""

import argparse
import json
import logging
import os
import sys
import time
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from reglens.config import get_settings
from reglens.evaluation.results import append_row, write_result
from reglens.ingestion.parse_pdf import TableMode, make_converter, parse_pdf

DEFAULT_PDF = Path("data/raw/reports/bnpp_fortis_pillar3_2025_tables.pdf")
CPUS = os.cpu_count() or 4
# (TableFormer mode, threads, OCR). The first one is the reference for cell comparison.
VARIANTS: list[tuple[TableMode, int, bool]] = [
    ("accurate", 4, False),
    ("accurate", CPUS, False),
    ("fast", CPUS, False),
    ("accurate", 4, True),
]


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--pages", type=int, nargs=2, default=[5, 9], metavar=("FIRST", "LAST"))
    args = parser.parse_args()
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.ERROR)
    first, last = args.pages
    n_pages = last - first + 1

    runs: list[dict[str, Any]] = []
    reference: list[list[list[str]]] | None = None
    for mode, threads, ocr in VARIANTS:
        t0 = time.perf_counter()
        converter = make_converter(do_ocr=ocr, table_mode=mode, num_threads=threads)
        init_s = time.perf_counter() - t0
        doc = parse_pdf(args.pdf, converter, doc_id="sample", langue="en",
                        source=args.pdf.as_posix(), page_range=(first, last))  # fmt: skip
        tables = [b.table.cells for b in doc.blocks if b.table is not None]
        reference = reference or tables
        total = sum(len(row) for t in reference for row in t)
        same = sum(
            ca == cb
            for ta, tb in zip(reference, tables, strict=False)
            for ra, rb in zip(ta, tb, strict=False)
            for ca, cb in zip(ra, rb, strict=False)
        )
        run = {
            "table_mode": mode,
            "threads": threads,
            "ocr": ocr,
            "init_s": round(init_s, 1),
            "convert_s": doc.parse_seconds,
            "s_per_page": round((doc.parse_seconds or 0) / n_pages, 1),
            "tables": len(tables),
            "table_shapes": [[len(t), len(t[0]) if t else 0] for t in tables],
            "cells_identical_to_reference": f"{same}/{total}",
        }
        runs.append(run)
        print(json.dumps(run), flush=True)

    result = {
        "name": "docling_speed",
        "date": date.today().isoformat(),
        "pdf": args.pdf.as_posix(),
        "pages": [first, last],
        "cpu_count": CPUS,
        "reference": "first variant (accurate, 4 threads, OCR off)",
        "runs": runs,
    }
    out = write_result(settings.results_dir, result)
    row = (
        f"| {result['date']} | docling_speed | 2 | Docling s/page on CPU ({n_pages} table pages): "
        "table mode x threads x OCR | "
        + "; ".join(
            f"{r['table_mode']}/{r['threads']}t/{'ocr' if r['ocr'] else 'no-ocr'}: "
            f"{r['s_per_page']} s/page, cells {r['cells_identical_to_reference']}"
            for r in runs
        )
        + f" | `{out.as_posix()}` |\n"
    )
    append_row(settings.results_dir, row)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Check Docling's tables against the PDF text layer, on a random sample of tables.

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/measure/check_tables.py              # uses every report already in the cache
    python scripts/measure/check_tables.py --n 20 --seed 42

For n tables chosen at random (fixed seed) and spread over the reports, every non-empty
cell must appear in the PDF text layer inside the table's box. Writes
results/<date>_table_cells.json (ids, counts and cell positions only: no table content).
The visual review of the final tables is scripts/measure/review_table_quality.py.
"""

import argparse
import sys
from datetime import date

from reglens.config import get_settings
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import (
    cached_reports,
    check_table,
    pick_tables,
    summarize,
    table_row,
)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20, help="tables checked")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    cached = cached_reports(settings)
    docs = {doc_id: docling for doc_id, (docling, _) in cached.items()}
    pdfs = {doc_id: pdf for doc_id, (_, pdf) in cached.items()}
    if not docs:
        print("no report in the Docling cache yet: run scripts/run_docling.py")
        return 1

    sample = pick_tables(docs, args.n, args.seed)
    checks = [check_table(pdfs[d], docs[d], t, d, i) for d, i, t in sample]
    for c in checks:
        status = "ok" if not c.missing else f"MISSING {len(c.missing)} at {c.missing[:6]}"
        print(f"{c.doc_id}#tbl-{c.table_index:<4} p{c.pages} {c.n_rows}x{c.n_cols} "
              f"cells {c.found}/{c.cells} {status}")  # fmt: skip
    total = summarize(checks)
    print(f"total: {total}")

    result = {
        "name": "table_cells",
        "date": date.today().isoformat(),
        "method": "every non-empty extracted cell must appear in the pypdfium2 text layer "
        "inside the table bbox (whitespace-insensitive); values only, not positions",
        "config": {"do_cell_matching": settings.docling_cell_matching},
        "seed": args.seed,
        "documents_in_cache": sorted(docs),
        "tables": [table_row(c) for c in checks],
        "total": total,
    }
    out = write_result(settings.results_dir, result)
    print(f"written: {out}")
    return 0 if total["found"] == total["cells"] else 1


if __name__ == "__main__":
    sys.exit(main())

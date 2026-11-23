"""Check Docling's tables against the PDFs: 20 tables automatically, 5 for manual review.

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/measure/check_tables.py              # uses every report already in the cache
    python scripts/measure/check_tables.py --n 20 --review 5 --seed 42

1. Values: for 20 tables chosen at random (fixed seed) and spread over the reports, every
   non-empty cell must appear in the PDF text layer inside the table's box. Writes
   results/<date>_table_cells.json (ids, counts and cell positions only: no table
   content) + one EXPERIMENTS row.
2. Structure: for the first 5 of them, writes data/cache/table_review/index.html showing
   the PDF region next to the extracted rows and columns, to check by eye. It contains
   unmasked report content, so it stays in data/cache/ (not committed, never ingested).
"""

import argparse
import html
import sys
from datetime import date

from reglens.config import get_settings
from reglens.evaluation.report_tables import cached_reports
from reglens.evaluation.results import append_row, write_result
from reglens.evaluation.table_check import (
    check_table,
    grid_html,
    pick_tables,
    render_region,
    summarize,
    table_row,
)

PAGE_STYLE = """
body { font: 14px system-ui, sans-serif; margin: 16px; background: #fff; color: #111; }
section { border-top: 2px solid #ccc; padding: 12px 0; }
.pair { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.pair > div { flex: 1 1 480px; overflow-x: auto; }
img { max-width: 100%; border: 1px solid #999; }
table.grid { border-collapse: collapse; font-size: 12px; }
.grid td, .grid th { border: 1px solid #bbb; padding: 2px 6px; vertical-align: top; }
.grid th { background: #eee; color: #555; font-weight: normal; }
.grid td.h { font-weight: bold; background: #f6f6ff; }
"""


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20, help="tables checked automatically")
    parser.add_argument("--review", type=int, default=5, help="tables for manual review")
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
        "manual_review": [f"{c.doc_id}#tbl-{c.table_index}" for c in checks[: args.review]],
    }
    out = write_result(settings.results_dir, result)
    row = (
        f"| {result['date']} | table_cells | 2 | Docling table values vs PDF text layer, "
        f"{len(checks)} tables spread over {len(docs)} cached report(s) | "
        f"{total['found']}/{total['cells']} cells found ({total['rate']}); missing by kind "
        f"{total['by_kind']}; flagged low-confidence {total['flagged_low_confidence']}, "
        f"missing caught by flag {total['missing_caught_by_flag']}; tables with a miss "
        f"{total['tables_with_a_miss']} | `{out.as_posix()}` |\n"
    )
    append_row(settings.results_dir, row)

    review_dir = settings.docling_cache_dir.parent / "table_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for (doc_id, index, table), c in list(zip(sample, checks, strict=True))[: args.review]:
        png = f"{doc_id}_tbl-{index}.png"
        render_region(pdfs[doc_id], docs[doc_id], table, review_dir / png)
        missing = f" · <b>cells not found in PDF text: {c.missing}</b>" if c.missing else ""
        sections.append(
            f"<section><h2>{html.escape(doc_id)}#tbl-{index} — page {c.pages}, "
            f"{c.n_rows} rows × {c.n_cols} columns</h2>"
            f"<p>Values: {c.found}/{c.cells} found in the PDF text{missing}</p>"
            f"<div class=pair><div><h3>PDF region</h3><img src='{png}'></div>"
            f"<div><h3>Extracted (r = row, c = column; bold = header)</h3>"
            f"{grid_html(table)}</div></div></section>"
        )
    page = (
        "<!doctype html><html><head><meta charset=utf-8><title>Table review</title>"
        f"<style>{PAGE_STYLE}</style></head><body><h1>Docling table review</h1>"
        "<p>Check that every value sits in the same row and column as in the PDF.</p>"
        + "\n".join(sections)
        + "</body></html>"
    )
    (review_dir / "index.html").write_text(page, encoding="utf-8")
    print(f"written: {out}\nreview:  {review_dir / 'index.html'}")
    return 0 if total["found"] == total["cells"] else 1


if __name__ == "__main__":
    sys.exit(main())

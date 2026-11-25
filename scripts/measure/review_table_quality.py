"""Visual review of the final report tables (what PostgreSQL holds and the chunks embed).

Usage (repository root, virtual environment active, after scripts/build_corpus.py):
    python scripts/measure/review_table_quality.py --n 20 --seed 42

Draws a reproducible random sample of the parsed report tables (final versions:
spreadsheet content for replaced tables, Docling's grid with inferred header rows
otherwise) and writes data/cache/table_quality_review/: the PDF region of each table next
to its stored grid (header rows in bold) and the header path of every column. That page
holds report content, so it stays in data/cache/. Each table is then labelled by eye:

    high    a reader would get a wrong answer: a value under the wrong column or row
            label, a header path naming the wrong year or measure, a wrong or missing value
    medium  meaning recoverable but degraded: header text truncated or fused, a missing
            column or row label, a section or title attached to the wrong rows, while
            every value keeps its correct label
    none    headers, labels and values match the PDF

The labels are added to the result file by hand; the high-severity rate is reported with
its 95 % Wilson interval (`wilson`).
"""

import argparse
import html
import math
import random
import sys
from datetime import date

from reglens.config import get_settings
from reglens.evaluation.report_tables import cached_reports, report_tables
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import render_region, tables_of
from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import header_paths


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval of a proportion k / n."""
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def grid_html(table: TableData) -> str:
    rows = []
    for r, row in enumerate(table.cells):
        tag = " class=h" if r < table.header_rows else ""
        cells = "".join(f"<td{tag}>{html.escape(x)}</td>" for x in row)
        rows.append(f"<tr><th>r{r}</th>{cells}</tr>")
    return "<table class=grid>" + "\n".join(rows) + "</table>"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tables = report_tables(settings.processed_dir)
    if not tables:
        print("no parsed report yet: run scripts/build_corpus.py")
        return 1
    sample = random.Random(args.seed).sample(tables, args.n)
    cached = cached_reports(settings, {t.doc_id for t in sample})
    out_dir = settings.docling_cache_dir.parent / "table_quality_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for rt in sample:
        t = rt.table
        docling, pdf = cached[rt.doc_id]
        item = tables_of(docling)[int(t.table_id.split("#tbl-")[1]) - 1]
        png = t.table_id.replace("#", "_") + ".png"
        render_region(pdf, docling, item, out_dir / png)
        paths = " | ".join(f"c{c}: {p}" for c, p in enumerate(header_paths(t)))
        sections.append(
            f"<section><h2>{html.escape(t.table_id)} (page {rt.page_number}, "
            f"{t.content_source}, {t.header_rows} header rows"
            f"{', inferred' if t.header_inferred else ''})</h2>"
            f"<div class=pair><div><img src='{png}'></div><div>{grid_html(t)}"
            f"<p>{html.escape(paths)}</p></div></div></section>"
        )
        print(f"{t.table_id}: page {rt.page_number}, {t.content_source}, "
              f"{t.n_rows}x{t.n_cols}, header {t.header_rows}")  # fmt: skip
    style = (
        "body{font:14px system-ui;margin:16px} .pair{display:flex;gap:16px;flex-wrap:wrap}"
        ".pair>div{flex:1 1 480px;overflow-x:auto} img{max-width:100%;border:1px solid #999}"
        ".grid{border-collapse:collapse;font-size:12px} .grid td,.grid th{border:1px solid "
        "#bbb;padding:2px 6px} td.h{font-weight:bold;background:#f6f6ff}"
    )
    (out_dir / "index.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Table quality</title><style>{style}"
        f"</style><h1>Final report tables</h1>{''.join(sections)}",
        encoding="utf-8",
    )
    result = {
        "name": "table_quality",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": f"random.Random({args.seed}).sample of {args.n} of the {len(tables)} final "
        "report tables; PDF region vs stored grid, labelled by eye (severity rules in "
        "scripts/measure/review_table_quality.py, fixed before the review)",
        "seed": args.seed,
        "sample": [rt.table.table_id for rt in sample],
    }
    print(f"written: {write_result(settings.results_dir, result)}\nreview: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

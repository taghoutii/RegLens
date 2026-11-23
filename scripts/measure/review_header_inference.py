"""Visual check of header-row inference on tables where Docling detected no header.

Usage (repository root, virtual environment active, after scripts/parse_corpus.py):
    python scripts/measure/review_header_inference.py --n 10 --seed 42

Counts, over every report table with 0 detected header rows, how many header rows the
rule of reglens/ingestion/table_rows.infer_header_rows gives; then draws a reproducible
sample of tables that receive a header and writes data/cache/header_inference_review/
(PDF region next to the grid, inferred header rows in bold). That page contains report
content, so it stays in data/cache/. The labels are added to the result by hand.
"""

import argparse
import html
import random
import sys
from collections import Counter
from datetime import date

from reglens.config import get_settings
from reglens.corpus.sources import Language
from reglens.evaluation.report_tables import cached_reports, report_tables
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import render_region, tables_of
from reglens.ingestion.models import TableData
from reglens.ingestion.table_rows import header_paths, infer_header_rows, with_header


def grid_html(table: TableData, n_header: int) -> str:
    rows = []
    for r, row in enumerate(table.cells):
        tag = " class=h" if r < n_header else ""
        cells = "".join(f"<td{tag}>{html.escape(x)}</td>" for x in row)
        rows.append(f"<tr><th>r{r}</th>{cells}</tr>")
    return "<table class=grid>" + "\n".join(rows) + "</table>"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    max_rows = settings.table_max_inferred_header_rows
    candidates: list[tuple[TableData, Language, int]] = []
    counts: Counter[int] = Counter()
    for rt in report_tables(settings.processed_dir):
        t = rt.table
        if t.header_inferred:  # parsed with inference: the header rows are the inferred ones
            n = t.header_rows
        elif t.header_rows == 0:  # parsed before inference was part of the pipeline
            n = infer_header_rows(t, rt.langue, max_rows)
        else:
            continue
        counts[n] += 1
        if n:
            candidates.append((t.model_copy(update={"header_rows": 0}), rt.langue, n))
    if not candidates:
        print("no table without a header: run scripts/parse_corpus.py")
        return 1
    sample = random.Random(args.seed).sample(candidates, min(args.n, len(candidates)))

    out_dir = settings.docling_cache_dir.parent / "header_inference_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    cached = cached_reports(settings, {t.table_id.split("#")[0] for t, _, _ in sample})
    sections = []
    for table, langue, n in sample:
        doc_id, index = table.table_id.split("#tbl-")
        docling, pdf = cached[doc_id]
        item = tables_of(docling)[int(index) - 1]
        png = f"{doc_id}_tbl-{index}.png"
        render_region(pdf, docling, item, out_dir / png)
        paths = header_paths(with_header(table, langue, max_rows))
        sections.append(
            f"<section><h2>{html.escape(table.table_id)}: {n} inferred header row(s)</h2>"
            f"<div class=pair><div><img src='{png}'></div><div>{grid_html(table, n)}"
            f"<p>Header paths: {html.escape(' | '.join(paths))}</p></div></div></section>"
        )
        print(f"{table.table_id}: {n} rows -> {' | '.join(paths)[:160]}")
    style = (
        "body{font:14px system-ui;margin:16px} .pair{display:flex;gap:16px;flex-wrap:wrap}"
        ".pair>div{flex:1 1 480px;overflow-x:auto} img{max-width:100%;border:1px solid #999}"
        ".grid{border-collapse:collapse;font-size:12px} .grid td,.grid th{border:1px solid "
        "#bbb;padding:2px 6px} td.h{font-weight:bold;background:#f6f6ff}"
    )
    (out_dir / "index.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Header inference</title><style>{style}"
        f"</style><h1>Inferred header rows (bold)</h1>{''.join(sections)}",
        encoding="utf-8",
    )

    result = {
        "name": "header_inference",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "tables with 0 detected header rows: leading rows with text beyond the "
        "first column and no number except years become header rows (at most 4), only if a "
        "row with numbers follows. Sample drawn with random.Random(seed).sample.",
        "seed": args.seed,
        "inferred_header_rows": {str(k): v for k, v in sorted(counts.items())},
        "tables_without_header": sum(counts.values()),
        "sample": [t.table_id for t, _, _ in sample],
    }
    print(f"written: {write_result(settings.results_dir, result)}\nreview: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
